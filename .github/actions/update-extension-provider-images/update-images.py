#!/usr/bin/env python3
#
# SPDX-FileCopyrightText: 2025 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
"""
Image Updater Script (Reusable GitHub Action)

Purpose:
This script is part of a reusable GitHub Action located in the same folder.
It automates the process of checking for and updating container image
versions defined in Gardener's extension provider repos at `imagevector/images.yaml`.

How it works:
1.  The action is triggered by a reusable workflow.
2.  It reads all image entries from the `images.yaml` file and parses comment directives.
3.  It uses the `oci.client` library to query container registries for new tags.
4.  It updates the `images.yaml` file in-place based on a set of rules:
    - For patch updates, it updates the tag of the existing entry.
    - For new minor/major versions, it adds a new image entry.
    - It ensures the highest minor version has a `targetVersion` of ">= X.Y".
    - For images without a `targetVersion`, it keeps only the single latest tag.
    - It respects comment directives that control update behavior.
5.  It generates a `release-notes.md` file containing direct links to the
    release pages for all new and intermediate tags.

Comment Directives (one per line, placed above image entries):
- freeze: Freeze this entry completely (no updates). Use cases:
    - Freeze a specific version due to a known issue in newer releases.
    - Keep a legacy entry (without targetVersion) as fallback for older k8s versions
      while allowing other entries with targetVersion to be updated normally.
- max-supported-k8s: Prevent adding new entries for k8s versions not yet supported by
    Gardener. Place on the highest version entry to stop automatic additions.
- version-mapping: GCP cloud-controller-manager only. Maps image major version to k8s
    minor version (e.g., image v32.x -> targetVersion 1.32.x).

Comments not recognized as directives are preserved in the output.

Manual k8s Constraints:
Images with targetVersion like '>= 1.34' that don't correlate with the image version
(e.g., csi-provisioner v6.x with '>= 1.34') are detected automatically. These entries
are updated to the latest available version while preserving the original targetVersion.

This script is not intended to be run manually by developers. It should be
invoked via the `gardener/cc-utils/.github/workflows/update-extension-provider-images.yaml`
reusable workflow.

Manual Intervention:
This script handles version *updates*. Manually editing `images.yaml` is still
required to remove entire image groups for deprecated Kubernetes versions that
are no longer supported.
"""

import argparse
import re
import sys
import traceback
from collections import defaultdict
from dataclasses import dataclass, asdict, replace
from typing import Optional, Any

import dacite
import semver
from ruamel.yaml import YAML
from ruamel.yaml import YAMLError

from oci import client as oci_client
from ocm import Label as OcmLabel
from version import parse_to_semver, is_final, iter_upgrade_path


# --- Data Classes ---
@dataclass
class ResourceId:
    name: str


@dataclass
class ImageEntry:
    name: str
    sourceRepository: str
    repository: str
    tag: str
    labels: list[OcmLabel]
    targetVersion: Optional[str] = None
    resourceId: Optional[ResourceId] = None
    _directives: Optional[dict[str, Any]] = None  # Internal field for directives

    def has_target_version(self) -> bool:
        return self.targetVersion is not None

    def update_tag(self, new_tag: str) -> None:
        self.tag = new_tag

    def update_target_version(self, new_target_version: str) -> None:
        self.targetVersion = new_target_version

    def copy_as_template(
            self,
            new_tag: str,
            new_target_version: str,
    ) -> 'ImageEntry':
        # Only keep version_mapping
        filtered_directives = {}
        if self._directives and self._directives.get('version_mapping'):
            filtered_directives['version_mapping'] = True

        return replace(
                self,
                tag=new_tag,
                targetVersion=new_target_version,
                _directives=filtered_directives if filtered_directives else None
        )

    def is_frozen(self) -> bool:
        return bool(self._directives and self._directives.get('freeze', False))

    def should_skip_minor_update(self) -> bool:
        return bool(self._directives and self._directives.get('max_supported_k8s', False))

    def has_version_mapping(self) -> bool:
        return bool(self._directives and self._directives.get('version_mapping', False))

    def has_manual_k8s_constraint(self) -> bool:
        """
        Check if this entry has a manual k8s version constraint.

        Returns True if:
        - Entry has a targetVersion starting with '>='
        - The constraint version doesn't match the image version
        - No version-mapping directive is present

        Example: image v6.2.0 with targetVersion '>= 1.34' is a manual constraint
        because 6.2 != 1.34 and there's no version-mapping.
        """
        if not self.targetVersion or self.has_version_mapping():
            return False

        if not self.targetVersion.startswith('>='):
            return False

        try:
            target_version_str = self.targetVersion.replace('>=', '').strip()
            target_version = parse_to_semver(target_version_str)
            image_version = parse_to_semver(self.tag)

            # If target version doesn't match image version, it's a manual constraint
            return (target_version.major, target_version.minor) != \
                   (image_version.major, image_version.minor)
        except ValueError:
            return False

    def set_directives(self, directives: dict[str, Any]) -> None:
        self._directives = directives or {}


@dataclass
class ImagesData:
    images: list[ImageEntry]

    def get_images_by_name(self) -> dict[str, list[ImageEntry]]:
        images_by_name = defaultdict(list)
        for image in self.images:
            images_by_name[image.name].append(image)
        return dict(images_by_name)

    def add_images(self, new_images: list[ImageEntry]) -> None:
        self.images.extend(new_images)

    def sort_images(self) -> None:
        """Sort images by name and then by semantic version."""

        def sort_key(image: ImageEntry) -> tuple[str, semver.Version]:
            try:
                return (image.name, parse_to_semver(image.tag))
            except ValueError:
                return (image.name, semver.Version(0))

        self.images.sort(key=sort_key)

    def to_dict(self) -> dict[str, Any]:
        result = {'images': []}
        for image in self.images:
            image_dict = asdict(image)
            # Remove internal fields that shouldn't end up in the new yaml
            image_dict.pop('_directives', None)
            result['images'].append(image_dict)

        return self._remove_none_values(result)

    def _remove_none_values(self, data: Any) -> Any:
        if isinstance(data, dict):
            return {
                k: self._remove_none_values(v)
                for k, v in data.items()
                if v is not None
            }
        elif isinstance(data, list):
            return [self._remove_none_values(item) for item in data]
        else:
            return data


@dataclass
class ImageUpdate:
    image_name: str
    new_tag: str
    update_type: str  # 'patch', 'minor', or 'singleton'
    old_tag: Optional[str] = None
    repository: Optional[str] = None


# --- Helper Functions ---
def load_and_validate_images_data(images_yaml_path: str) -> tuple[ImagesData, Any]:
    """
    Load and validate images.yaml data using dacite.

    Args:
        images_yaml_path: Path to the images.yaml file

    Returns:
        Tuple of (Validated ImagesData object, raw YAML data for comment parsing)

    Raises:
        ValueError: If the file cannot be read, parsed, or validated
    """
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096

    with open(images_yaml_path, "r") as f:
        raw_yaml_data = yaml.load(f)
        raw_yaml_data['images']

    try:
        validated_data = dacite.from_dict(
            data_class=ImagesData,
            data=dict(raw_yaml_data),  # Convert to regular dict for dacite
            config=dacite.Config(
                strict=True,
                check_types=True,
            ),
        )
        return validated_data, raw_yaml_data
    except dacite.DaciteError as e:
        raise ValueError(f"Invalid images.yaml structure in {images_yaml_path}: {e}")


def parse_image_comments(yaml_content: str) -> dict[str, dict[str, Any]]:
    """
    Parse comments above image entries to extract directives.

    Scans through the YAML content line by line, collecting comment blocks
    that appear immediately before image entries (lines starting with '- name:').
    Each comment block is parsed for supported directives.

    Args:
        yaml_content: Raw YAML file content as string

    Returns:
        Dictionary mapping line numbers to directive information, where each
        value contains the image name and parsed directives
    """
    lines = yaml_content.split('\n')
    image_directives = {}
    current_comment_block = []

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Collect comment lines
        if stripped.startswith('#'):
            current_comment_block.append(stripped[1:].strip())
        elif stripped.startswith('- name:'):
            # This is an image entry, parse the preceding comments
            image_name_match = re.search(r'- name:\s*(.+)', stripped)
            if image_name_match:
                image_name = image_name_match.group(1).strip()
                directives = parse_comment_directives(current_comment_block)
                if directives:
                    # Use line number as unique key since image names can repeat
                    image_directives[i] = {
                        'name': image_name,
                        'directives': directives
                    }
            current_comment_block = []

    return image_directives


def parse_comment_directives(comment_lines: list[str]) -> dict[str, Any]:
    """
    Parse individual comment directives from a block of comment lines.

    Recognizes the following directives (case-insensitive, one per line):
    - freeze: Freezes this entry completely (no updates)
    - max-supported-k8s: Prevents minor/major version updates
    - version-mapping: Enables special version mapping (e.g., for K8s CCM)

    All other line comments are preserved in additional_comments.

    Args:
        comment_lines: List of comment line contents (without '#' prefix)

    Returns:
        Dictionary of recognized directives with boolean values
    """
    directives = {}
    additional_comments = []

    for line in comment_lines:
        cleaned_line = line.strip().lstrip('#').strip()
        line_lower = cleaned_line.lower()

        # Check if the line is a standalone directive
        if line_lower == 'freeze':
            directives['freeze'] = True
        elif line_lower == 'max-supported-k8s':
            directives['max_supported_k8s'] = True
        elif line_lower == 'version-mapping':
            directives['version_mapping'] = True
        elif cleaned_line:
            additional_comments.append(cleaned_line)

    # Save additional comments if any
    if additional_comments:
        directives['additional_comments'] = additional_comments

    return directives


def create_image_directive_map(
    yaml_content: str,
    images_data: ImagesData
) -> dict[str, dict[str, Any]]:
    """
    Create a mapping from image entries to their comment directives.

    Converts the line-number-based directive mapping to an image-entry-based
    mapping by matching image names and their occurrence order. This allows
    directives to be applied to specific ImageEntry objects.

    Args:
        yaml_content: Raw YAML file content as string
        images_data: Parsed and validated ImagesData object

    Returns:
        Dictionary mapping unique image keys (name:repository:tag) to their
        comment directives
    """
    comment_directives = parse_image_comments(yaml_content)

    # Convert line-based mapping to image-based mapping
    lines = yaml_content.split('\n')
    image_directives = {}

    for line_num, directive_info in comment_directives.items():
        # Find the corresponding image entry by matching name and position
        image_name = directive_info['name']

        # Count which occurrence of this image name this is
        occurrence = 0
        for i in range(int(line_num)):
            if re.search(rf'- name:\s*{re.escape(image_name)}', lines[i]):
                occurrence += 1

        # Find the corresponding ImageEntry object
        matching_images = [img for img in images_data.images if img.name == image_name]
        if occurrence < len(matching_images):
            # Create a unique key for this specific image entry
            img_entry = matching_images[occurrence]
            key = f"{img_entry.name}:{img_entry.repository}:{img_entry.tag}"
            image_directives[key] = directive_info['directives']

    return image_directives


# --- Core Logic Functions ---
def find_greater_versions(
    current_tags: list[str],
    available_tags: list[str],
) -> dict[str, list[str]]:
    """
    Compare current tags with available tags to find greater patch and minor/major versions.

    This function analyzes semantic versions to categorize available updates:
    - Patch updates: Same major.minor, higher patch version
    - Minor/Major updates: Higher major or minor version

    Args:
        current_tags: A list of current tag strings from images.yaml
        available_tags: A list of all available tag strings from the repository

    Returns:
        A dictionary with 'patch' and 'minor' keys containing lists of new tags as strings
    """
    available_versions_map: dict[semver.Version, str] = {}
    for tag in available_tags:
        try:
            ver = parse_to_semver(tag)
            if is_final(ver):
                available_versions_map[ver] = tag
        except ValueError:
            continue

    available_versions = sorted(available_versions_map.keys())

    current_versions: list[semver.Version] = []
    for tag in current_tags:
        try:
            current_versions.append(parse_to_semver(tag))
        except ValueError:
            continue
    current_versions.sort()

    if not current_versions:
        existing_minor_tags = [available_versions_map[ver] for ver in available_versions]
        return {
            "patch": [],
            "minor": existing_minor_tags,
        }

    highest_current_ver = current_versions[-1]

    highest_patch_for_minor: dict[tuple[int, int], semver.Version] = defaultdict(
            lambda: semver.Version(0)
    )
    for ver in current_versions:
        key = (ver.major, ver.minor)
        if ver > highest_patch_for_minor[key]:
            highest_patch_for_minor[key] = ver

    greater_patch_tags: list[str] = []
    greater_minor_tags: list[str] = []

    for ver in available_versions:
        original_tag = available_versions_map[ver]
        key = (ver.major, ver.minor)

        if key in highest_patch_for_minor:
            if ver > highest_patch_for_minor[key]:
                greater_patch_tags.append(original_tag)
        elif ver > highest_current_ver:
            greater_minor_tags.append(original_tag)

    return {
        "patch": sorted(greater_patch_tags, key=parse_to_semver),
        "minor": sorted(greater_minor_tags, key=parse_to_semver),
    }


def update_singleton_image(
    image_list: list[ImageEntry],
    all_greater_tags: list[str],
    name: str
) -> list[ImageUpdate]:
    """
    Handle updates for images without targetVersion (singleton components).

    Singleton images are components that don't track multiple versions simultaneously.
    They should have exactly one entry and are updated to the latest available version.

    Args:
        image_list: List of image entries for this component (should contain exactly 1 item)
        all_greater_tags: All available newer tags (combination of patch and minor updates)
        name: Image name for logging and update tracking

    Returns:
        List containing zero or one ImageUpdate object

    Raises:
        SystemExit: If multiple entries found for singleton image (configuration error)
    """
    if not all_greater_tags:
        return []

    if len(image_list) != 1:
        print(
            f"Error: Found {len(image_list)} entries for singleton image '{name}' "
            f"but expected 1. Aborting.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Check if this singleton image should be kept
    if image_list[0].is_frozen():
        print(f"Skipping update for singleton {name} due to 'freeze' directive", file=sys.stderr)
        return []

    latest_tag = max(all_greater_tags, key=parse_to_semver)
    old_tag = image_list[0].tag

    if old_tag != latest_tag:
        image_list[0].update_tag(latest_tag)
        return [ImageUpdate(
            image_name=name,
            old_tag=old_tag,
            new_tag=latest_tag,
            update_type="singleton",
            repository=image_list[0].repository,
        )]

    return []


def apply_patch_updates(
    image_list: list[ImageEntry],
    patch_tags: list[str],
    name: str
) -> list[ImageUpdate]:
    """
    Apply patch version updates to existing image entries.
    Only creates one update record per major.minor showing the final patch version jump.

    Finds entries with the same major.minor version and updates them to the latest patch.
    Only updates existing entries, does not create new ones.

    Args:
        image_list: List of image entries for this component
        patch_tags: List of patch version tags to apply
        name: Image name for logging and update tracking

    Returns:
        List of ImageUpdate objects representing the applied patch updates
    """
    updates: list[ImageUpdate] = []

    # Group patch tags by major.minor version
    tags_by_version: dict[tuple[int, int], list[str]] = defaultdict(list)
    for tag in patch_tags:
        try:
            version = parse_to_semver(tag)
            key = (version.major, version.minor)
            tags_by_version[key].append(tag)
        except ValueError:
            continue

    # For each major.minor, find the corresponding image entry and update to highest patch
    for (major, minor), tags in tags_by_version.items():
        highest_patch_tag = max(tags, key=parse_to_semver)

        # Find the image entry with matching major.minor
        for img_entry in image_list:
            try:
                img_version = parse_to_semver(img_entry.tag)
                if (img_version.major, img_version.minor) == (major, minor):
                    # Check if this specific image should skip patch updates
                    if img_entry.is_frozen():
                        print(
                            f"Skipping patch update for {name} tag {img_entry.tag} "
                            "due to 'freeze' directive",
                            file=sys.stderr,
                        )
                        continue

                    old_tag = img_entry.tag
                    img_entry.update_tag(highest_patch_tag)
                    updates.append(ImageUpdate(
                        image_name=name,
                        old_tag=old_tag,
                        new_tag=highest_patch_tag,
                        update_type="patch",
                        repository=img_entry.repository,
                    ))
                    break
            except ValueError:
                continue

    return updates


def create_minor_version_entries(
    image_list: list[ImageEntry],
    minor_tags: list[str],
    name: str
) -> tuple[list[ImageUpdate], list[ImageEntry]]:
    """
    Create new image entries for minor/major version updates.
    Only creates one entry per major.minor (the highest patch version).

    Creates new entries using the first existing entry as a template,
    updating the tag and targetVersion fields appropriately.

    Args:
        image_list: List of existing image entries for this component
        minor_tags: List of minor/major version tags to add
        name: Image name for logging and update tracking

    Returns:
        Tuple containing:
        - List of ImageUpdate objects for minor version additions
        - List of new ImageEntry objects to be added to the main data structure
    """
    updates: list[ImageUpdate] = []
    new_entries: list[ImageEntry] = []

    # Check if the highest version entry (which controls minor updates) should be kept
    try:
        highest_existing = max(image_list, key=lambda img: parse_to_semver(img.tag))
        if highest_existing.should_skip_minor_update():
            print(
                f"Skipping minor/major updates for {name} due to "
                "'max-supported-k8s' directive on highest version",
                file=sys.stderr,
            )
            return updates, new_entries
    except ValueError:
        pass  # If we can't parse versions, proceed with updates

    # Group minor tags by major.minor version
    tags_by_version: dict[tuple[int, int], list[str]] = defaultdict(list)
    for tag in minor_tags:
        try:
            version = parse_to_semver(tag)
            key = (version.major, version.minor)
            tags_by_version[key].append(tag)
        except ValueError:
            continue

    # For each major.minor, use only the highest patch version
    for (major, minor), tags in tags_by_version.items():
        highest_tag = max(tags, key=parse_to_semver)

        # Create new entry for the highest patch version only
        if image_list[0].has_version_mapping():
            target_version = f"1.{major}.x"  # For k8s-ccm mapping
        else:
            target_version = f"{major}.{minor}.x"

        new_entry = image_list[-1].copy_as_template(highest_tag, target_version)
        new_entries.append(new_entry)

        # Find the previous highest version for the update record
        all_tags = [img.tag for img in image_list] + [entry.tag for entry in new_entries]
        previous_tags = []
        for tag in all_tags:
            try:
                version = parse_to_semver(tag)
                if version < parse_to_semver(highest_tag):
                    previous_tags.append(tag)
            except ValueError:
                continue

        old_tag = max(previous_tags, key=parse_to_semver) if previous_tags else None
        updates.append(ImageUpdate(
            image_name=name,
            old_tag=old_tag,
            new_tag=highest_tag,
            update_type="minor",
            repository=new_entry.repository,
        ))

    return updates, new_entries


def update_target_versions(
    existing_entries: list[ImageEntry],
    new_entries: list[ImageEntry]
) -> None:
    """
    Update targetVersion fields when new entries are added.

    Updates the previously highest version (from ">= X.Y" to "X.Y.x"),
    new version entries to "X.Y.x" format,
    and sets the new highest version to ">= X.Y" format.

    Args:
        existing_entries: The original image entries for this component
        new_entries: The newly created image entries
    """
    if not new_entries:
        return

    # Find the previously highest version among existing entries
    parseable_existing = []
    for entry in existing_entries:
        try:
            parse_to_semver(entry.tag)
            parseable_existing.append(entry)
        except ValueError:
            continue

    # Find the new highest version among all entries
    parseable_new = []
    for entry in new_entries:
        try:
            parse_to_semver(entry.tag)
            parseable_new.append(entry)
        except ValueError:
            continue

    if not parseable_existing or not parseable_new:
        return

    # Get the previously highest existing entry
    previously_highest = max(parseable_existing, key=lambda img: parse_to_semver(img.tag))

    # Get the new overall highest entry (could be existing or new)
    all_parseable = parseable_existing + parseable_new
    new_highest = max(all_parseable, key=lambda img: parse_to_semver(img.tag))

    # Update the previously highest version from ">= X.Y" to "X.Y.x"
    prev_version = parse_to_semver(previously_highest.tag)
    if previously_highest.has_version_mapping():
        previously_highest.update_target_version(f"1.{prev_version.major}.x")
    else:
        previously_highest.update_target_version(f"{prev_version.major}.{prev_version.minor}.x")

    # Set all new entries to "X.Y.x" format
    for entry in parseable_new:
        version = parse_to_semver(entry.tag)
        if entry.has_version_mapping():
            entry.update_target_version(f"1.{version.major}.x")
        else:
            entry.update_target_version(f"{version.major}.{version.minor}.x")

    # Set the new highest entry to ">= X.Y" format
    new_highest_version = parse_to_semver(new_highest.tag)
    if new_highest.has_version_mapping():
        new_highest.update_target_version(f">= 1.{new_highest_version.major}")
    else:
        major_minor = f"{new_highest_version.major}.{new_highest_version.minor}"
        new_highest.update_target_version(f">= {major_minor}")


def update_manual_constraint_images(
    image_list: list[ImageEntry],
    all_greater_tags: list[str],
    name: str
) -> list[ImageUpdate]:
    """
    Handle updates for images with manual k8s version constraints.

    These are images where the targetVersion (e.g., '>= 1.34') doesn't correlate
    with the image version (e.g., v6.2.0). In this case:
    - Update the constrained entry's tag to the latest available version
    - Preserve the original targetVersion (don't create new entries)
    - Apply patch updates to any other entries without the constraint

    Args:
        image_list: List of image entries for this component
        all_greater_tags: All available newer tags (patch + minor combined)
        name: Image name for logging and update tracking

    Returns:
        List of ImageUpdate objects representing the applied updates
    """
    if not all_greater_tags:
        return []

    updates: list[ImageUpdate] = []

    # Find the entry with the manual constraint (the '>= X.Y' one)
    constraint_entry = None
    other_entries = []
    for entry in image_list:
        if entry.has_manual_k8s_constraint():
            constraint_entry = entry
        else:
            other_entries.append(entry)

    # Update the constraint entry to the latest available version
    if constraint_entry:
        latest_tag = max(all_greater_tags, key=parse_to_semver)
        old_tag = constraint_entry.tag

        if old_tag != latest_tag:
            constraint_entry.update_tag(latest_tag)
            updates.append(ImageUpdate(
                image_name=name,
                old_tag=old_tag,
                new_tag=latest_tag,
                update_type="singleton",
                repository=constraint_entry.repository,
            ))
            print(
                f"Updated {name} with manual k8s constraint: {old_tag} -> {latest_tag} "
                f"(keeping targetVersion: {constraint_entry.targetVersion})",
                file=sys.stderr,
            )

    # Apply patch updates to other entries (non-constraint ones)
    if other_entries:
        # Group all_greater_tags by major.minor to find patches for existing entries
        tags_by_version: dict[tuple[int, int], list[str]] = defaultdict(list)
        for tag in all_greater_tags:
            try:
                version = parse_to_semver(tag)
                key = (version.major, version.minor)
                tags_by_version[key].append(tag)
            except ValueError:
                continue

        for entry in other_entries:
            if entry.is_frozen():
                continue
            try:
                entry_version = parse_to_semver(entry.tag)
                key = (entry_version.major, entry_version.minor)
                if key in tags_by_version:
                    highest_patch = max(tags_by_version[key], key=parse_to_semver)
                    if entry.tag != highest_patch:
                        old_tag = entry.tag
                        entry.update_tag(highest_patch)
                        updates.append(ImageUpdate(
                            image_name=name,
                            old_tag=old_tag,
                            new_tag=highest_patch,
                            update_type="patch",
                            repository=entry.repository,
                        ))
            except ValueError:
                continue

    return updates


def update_versioned_images(
    image_list: list[ImageEntry],
    greater: dict[str, list[str]],
    name: str
) -> tuple[list[ImageUpdate], list[ImageEntry]]:
    """
    Handle updates for images with targetVersion (versioned components).

    1. Applies patch updates to existing entries
    2. Creates new entries for minor/major versions
    3. Updates all targetVersion fields

    Args:
        image_list: List of image entries for this component
        greater: Dictionary with 'patch' and 'minor' version lists
        name: Image name for logging and update tracking

    Returns:
        Tuple containing:
        - List of ImageUpdate objects for all updates (patch + minor)
        - List of new ImageEntry objects to be added
    """
    all_updates: list[ImageUpdate] = []

    # Apply patch updates to existing entries
    patch_updates = apply_patch_updates(image_list, greater["patch"], name)
    all_updates.extend(patch_updates)

    # Create new entries for minor/major versions
    minor_updates, new_entries = create_minor_version_entries(image_list, greater["minor"], name)
    all_updates.extend(minor_updates)

    # Update targetVersion fields
    update_target_versions(image_list, new_entries)

    return all_updates, new_entries


def update_images_data(
    images_data: ImagesData,
    new_versions_by_name_repo: dict[tuple[str, str], dict[str, list[str]]],
    image_directives: dict[str, dict[str, Any]]
) -> list[ImageUpdate]:
    """
    Update the ImagesData structure with new versions and return structured update info.

    Main function that processes all images and applies updates according to their
    type (singleton vs versioned). It modifies the images_data in-place
    and returns a comprehensive list of all changes made.

    Frozen entries (with 'freeze' directive) are excluded from processing entirely.
    This allows a frozen entry without targetVersion to coexist with active versioned
    entries for the same image name.

    Args:
        images_data: The ImagesData object loaded from images.yaml
        new_versions_by_name: Maps (image_name, repository) to its new 'patch' and 'minor' versions
        image_directives: Maps image entries to their comment directives

    Returns:
        List of structured ImageUpdate objects detailing each update performed
    """
    all_updates: list[ImageUpdate] = []
    all_new_entries: list[ImageEntry] = []

    # Apply directives to image entries
    for image in images_data.images:
        key = f"{image.name}:{image.repository}:{image.tag}"
        if key in image_directives:
            image.set_directives(image_directives[key])

    # Group images by (name, repository) for processing
    images_by_name_repo = defaultdict(list)
    for image in images_data.images:
        images_by_name_repo[(image.name, image.repository)].append(image)

    # Process each image group that has new versions available
    for (name, repository), image_list in images_by_name_repo.items():
        if (name, repository) not in new_versions_by_name_repo:
            continue

        greater = new_versions_by_name_repo[(name, repository)]

        # Separate frozen entries from active entries
        frozen_entries = [img for img in image_list if img.is_frozen()]
        active_entries = [img for img in image_list if not img.is_frozen()]

        # Log frozen entries
        for img in frozen_entries:
            print(f"Skipping frozen entry {name} tag {img.tag}", file=sys.stderr)

        # If no active entries remain, nothing to update
        if not active_entries:
            continue

        has_target_version = any(img.has_target_version() for img in active_entries)
        has_manual_constraint = any(img.has_manual_k8s_constraint() for img in active_entries)

        if not has_target_version:
            # Handle singleton images (no targetVersion)
            all_greater_tags = greater["patch"] + greater["minor"]
            updates = update_singleton_image(active_entries, all_greater_tags, name)
            all_updates.extend(updates)
        elif has_manual_constraint:
            # Handle images with manual k8s constraints (e.g., '>= 1.34' with v6.x image)
            all_greater_tags = greater["patch"] + greater["minor"]
            updates = update_manual_constraint_images(active_entries, all_greater_tags, name)
            all_updates.extend(updates)
        else:
            # Handle versioned images (with targetVersion)
            updates, new_entries = update_versioned_images(active_entries, greater, name)
            all_updates.extend(updates)
            all_new_entries.extend(new_entries)

    # Add all new entries to the main data structure
    images_data.add_images(all_new_entries)
    return all_updates


# --- I/O and Formatting Functions ---
def write_yaml_file(
        data: ImagesData,
        filename: str,
        original_yaml_data: Any
) -> None:
    """
    Write the ImagesData and comments to a YAML file.

    Args:
        data: The ImagesData object to be written
        filename: The path to the output YAML file
        original_yaml_data: The original YAML data structure with comments
    """
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096

    # Create completely new structure
    new_data = yaml.load("images: []\n")
    updated_data = data.to_dict()

    # Add each image with comment preservation
    for i, img_data in enumerate(updated_data['images']):
        clean_img = {k: v for k, v in img_data.items() if k != '_directives'}
        new_data['images'].append(clean_img)

        # Reconstruct comments from the original ImageEntry's directives
        image_entry = data.images[i]

        if image_entry._directives:
            comment_lines = []

            # Add directive comments
            if image_entry._directives.get('freeze'):
                comment_lines.append("freeze")
            if image_entry._directives.get('max_supported_k8s'):
                comment_lines.append("max-supported-k8s")
            if image_entry._directives.get('version_mapping'):
                comment_lines.append("version-mapping")

            # Add additional comments (non-directive comments)
            if image_entry._directives.get('additional_comments'):
                comment_lines.extend(image_entry._directives['additional_comments'])

            # Apply all comments
            if comment_lines:
                comment_text = "\n".join(comment_lines)
                new_data['images'].yaml_set_comment_before_after_key(
                    key=i,
                    before=comment_text,
                    indent=0
                )

    with open(filename, "w") as f:
        yaml.dump(new_data, f)


def create_release_notes(
    updates: list[ImageUpdate],
    images_data: ImagesData,
    all_available_tags: dict[tuple[str, str], list[str]],
    filename: str,
) -> None:
    """
    Create a markdown file with links to release notes of all added or changed releases.

    Generates comprehensive release notes including intermediate versions to ensure
    no breaking changes are missed during updates. Links directly to GitHub release pages.

    Args:
        updates: A list of structured update objects
        images_data: The updated ImagesData (used to find source repositories)
        all_available_tags: A map of (image_name, repository) to all their available tags
        filename: The path to the output release notes markdown file
    """
    if not updates:
        return

    repos_by_name_repo = {}
    for img in images_data.images:
        if img.sourceRepository:
            repos_by_name_repo[(img.name, img.repository)] = img.sourceRepository

    # Group updates by (image_name, repository) to handle multiple repos per image name
    updates_by_image_repo: dict[tuple[str, str, str], list[str]] = defaultdict(list)

    for update in updates:
        image_name = update.image_name
        old_tag = update.old_tag
        new_tag = update.new_tag
        repository = update.repository

        if old_tag:
            try:
                if not repository or (image_name, repository) not in all_available_tags:
                    continue

                valid_tags = []
                for t in all_available_tags[(image_name, repository)]:
                    try:
                        v = parse_to_semver(t)
                        if is_final(v):
                            valid_tags.append(t)
                    except ValueError:
                        continue

                intermediate = list(
                    iter_upgrade_path(
                        whence=old_tag,
                        whither=new_tag,
                        versions=valid_tags,
                    )
                )

                source_repo = repos_by_name_repo.get((image_name, repository), "")
                key = (image_name, repository, source_repo)
                updates_by_image_repo[key].extend(intermediate)
            except ValueError as e:
                print(f"Error: Could not determine upgrade path for {image_name} ")
                print(f" with update {update}: {e}", file=sys.stderr)
                sys.exit(1)
        else:
            if not repository:
                continue
            source_repo = repos_by_name_repo.get((image_name, repository), "")
            key = (image_name, repository, source_repo)
            updates_by_image_repo[key].append(new_tag)

    with open(filename, "w") as f:
        f.write("## Release Notes\n\n")
        f.write(
            "The following images have been updated. Please review the release notes for each "
            "component to check if changes need to be made to our Helm charts:\n\n"
        )
        f.write(
            "**Note**: All intermediate versions between the current and new version are listed "
            "to ensure no breaking changes are missed.\n\n"
        )

        # Group by image name for display, but keep repository separation
        by_image_name = defaultdict(list)
        for (image_name, repository, source_repo), tags in updates_by_image_repo.items():
            by_image_name[image_name].append((repository, source_repo, tags))

        for image_name in sorted(by_image_name.keys()):
            f.write(f"### {image_name}\n\n")

            # Sort by repository to have consistent output
            repo_data = sorted(by_image_name[image_name], key=lambda x: x[0])

            for repository, source_repo, tags in repo_data:
                unique_tags = sorted(list(set(tags)), key=parse_to_semver)

                # Add repository info if multiple repos for same image
                if len(repo_data) > 1:
                    f.write(f"**{repository}:**\n")

                for tag in unique_tags:
                    if source_repo:
                        release_link = f"https://{source_repo}/releases/tag/{tag}"
                        f.write(f"- [{tag}]({release_link})\n")
                    else:
                        f.write(f"- {tag}\n")

                if len(repo_data) > 1:
                    f.write("\n")

            f.write("\n")


# --- Main Execution ---
def main() -> None:
    """Main execution function."""

    print("Starting image update process...", file=sys.stderr)

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--images-yaml-path",
        required=True,
        help="Path to the images.yaml file.",
    )
    parser.add_argument(
        "--release-notes-path",
        required=True,
        help="Path where the release notes markdown file will be generated.",
    )
    args = parser.parse_args()

    # Read the raw YAML content first (for comment parsing)
    try:
        with open(args.images_yaml_path, 'r') as f:
            yaml_content = f.read()
        images_data, original_yaml_data = load_and_validate_images_data(args.images_yaml_path)
    except (FileNotFoundError, YAMLError, ValueError) as e:
        print(f"Error loading images data: {e}", file=sys.stderr)
        sys.exit(1)

    # Parse comment directives
    image_directives = create_image_directive_map(yaml_content, images_data)

    oci_api = oci_client.client_with_dockerauth()

    image_groups: dict[tuple[str, str], list[ImageEntry]] = defaultdict(list)
    for image in images_data.images:
        image_groups[(image.name, image.repository)].append(image)

    all_new_versions: dict[tuple[str, str], dict[str, list[str]]] = {}
    all_available_tags: dict[tuple[str, str], list[str]] = {}

    for (name, repository), image_list in image_groups.items():
        print(f"Checking {name} at {repository}...", file=sys.stderr)

        try:
            available_tags = oci_api.tags(image_reference=repository)
        except Exception:
            print(f"Critical: Failed to retrieve tags for {repository}. Aborting.", file=sys.stderr)
            traceback.print_exc()
            sys.exit(1)

        if not available_tags:
            print(
                f"Error: Repository '{repository}' returned an empty list of tags. "
                "This indicates a configuration error or a major upstream issue. Aborting.",
                file=sys.stderr,
            )
            sys.exit(1)

        all_available_tags[(name, repository)] = available_tags
        current_tags = [img.tag for img in image_list]

        greater_versions = find_greater_versions(current_tags, available_tags)

        if greater_versions["patch"] or greater_versions["minor"]:
            print(f"New tags found for {name}:", file=sys.stderr)
            if greater_versions["patch"]:
                print(f"  Patch updates: {', '.join(greater_versions['patch'])}", file=sys.stderr)
            if greater_versions["minor"]:
                print(
                    f"  Minor/Major updates: {', '.join(greater_versions['minor'])}", file=sys.stderr
                )
            all_new_versions[(name, repository)] = greater_versions

    if all_new_versions:
        updates = update_images_data(images_data, all_new_versions, image_directives)

        if updates:
            images_data.sort_images()
            write_yaml_file(images_data, args.images_yaml_path, original_yaml_data)
            print(f"\nUpdated {args.images_yaml_path} with {len(updates)} changes.", file=sys.stderr)

        create_release_notes(updates, images_data, all_available_tags, args.release_notes_path)
        print(f"Created {args.release_notes_path}", file=sys.stderr)

        print("The following container images have been updated:")
        for update in updates:
            print(
                f"  - {update.image_name}: {update.old_tag or 'N/A'} -> "
                f"{update.new_tag} ({update.update_type})"
            )
    else:
        print("\nNo greater versions found for any images.", file=sys.stderr)


if __name__ == "__main__":
    main()
