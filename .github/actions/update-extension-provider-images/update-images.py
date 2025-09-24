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
2.  It reads all image entries from the `images.yaml` file.
3.  It uses the `oci.client` library to query container registries for new tags.
4.  It updates the `images.yaml` file in-place based on a set of rules:
    - For patch updates, it updates the tag of the existing entry.
    - For new minor/major versions, it adds a new image entry.
    - It ensures the highest minor version has a `targetVersion` of ">= X.Y".
    - For images without a `targetVersion`, it keeps only the single latest tag.
5.  It generates a `release-notes.md` file containing direct links to the
    release pages for all new and intermediate tags.

This script is not intended to be run manually by developers. It should be
invoked via the `gardener/cc-utils/.github/workflows/update-extension-provider-images.yaml`
reusable workflow.

Manual Intervention:
This script handles version *updates*. Manually editing `images.yaml` is still
required to remove entire image groups for deprecated Kubernetes versions that
are no longer supported.
"""

import argparse
import sys
import traceback
from collections import defaultdict
from dataclasses import dataclass, asdict, replace
from typing import Optional, Any

import dacite
import semver
import yaml

from oci import client as oci_client
from version import parse_to_semver, is_final, iter_upgrade_path


# --- Data Classes ---
@dataclass
class LabelValue:
    network_exposure: str
    authentication_enforced: bool
    user_interaction: str
    confidentiality_requirement: str
    integrity_requirement: str
    availability_requirement: str


@dataclass
class Label:
    name: str
    value: LabelValue


@dataclass
class ResourceId:
    name: str


@dataclass
class ImageEntry:
    name: str
    sourceRepository: str
    repository: str
    tag: str
    labels: list[Label]
    targetVersion: Optional[str] = None
    resourceId: Optional[ResourceId] = None

    def has_target_version(self) -> bool:
        return self.targetVersion is not None

    def update_tag(self, new_tag: str) -> None:
        self.tag = new_tag

    def update_target_version(self, new_target_version: str) -> None:
        self.targetVersion = new_target_version

    def copy_as_template(
            self,
            new_tag: str,
            new_target_version: str
    ) -> 'ImageEntry':
        return replace(
                self,
                tag=new_tag,
                targetVersion=new_target_version
        )


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
        result = asdict(self)
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


# --- Helper Functions ---
def load_and_validate_images_data(images_yaml_path: str) -> ImagesData:
    """
    Load and validate images.yaml data using dacite.

    Args:
        images_yaml_path: Path to the images.yaml file

    Returns:
        Validated ImagesData object

    Raises:
        ValueError: If the file cannot be read, parsed, or validated
    """
    with open(images_yaml_path, "r") as f:
        raw_data = yaml.safe_load(f)

    try:
        return dacite.from_dict(
            data_class=ImagesData,
            data=raw_data,
            config=dacite.Config(
                strict=True,
                check_types=True,
            ),
        )
    except dacite.DaciteError as e:
        raise ValueError(f"Invalid images.yaml structure in {images_yaml_path}: {e}")


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

    latest_tag = max(all_greater_tags, key=parse_to_semver)
    old_tag = image_list[0].tag

    if old_tag != latest_tag:
        image_list[0].update_tag(latest_tag)
        return [ImageUpdate(
            image_name=name,
            old_tag=old_tag,
            new_tag=latest_tag,
            update_type="singleton",
        )]

    return []


def apply_patch_updates(
    image_list: list[ImageEntry],
    patch_tags: list[str],
    name: str
) -> list[ImageUpdate]:
    """
    Apply patch version updates to existing image entries.

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

    for patch_tag in patch_tags:
        try:
            patch_version = parse_to_semver(patch_tag)
        except ValueError:
            continue

        for img_entry in image_list:
            try:
                img_version = parse_to_semver(img_entry.tag)
                if (img_version.major, img_version.minor) == (
                    patch_version.major,
                    patch_version.minor,
                ):
                    old_tag = img_entry.tag
                    img_entry.update_tag(patch_tag)
                    updates.append(ImageUpdate(
                        image_name=name,
                        old_tag=old_tag,
                        new_tag=patch_tag,
                        update_type="patch",
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

    for minor_tag in minor_tags:
        try:
            minor_version = parse_to_semver(minor_tag)
        except ValueError:
            continue

        # Create new entry based on template
        target_version = f"{minor_version.major}.{minor_version.minor}.x"
        new_entry = image_list[0].copy_as_template(minor_tag, target_version)
        new_entries.append(new_entry)

        # Find the previous highest version for the update record
        all_tags = [img.tag for img in image_list] + [entry.tag for entry in new_entries]

        previous_tags = []
        for tag in all_tags:
            try:
                version = parse_to_semver(tag)
                if version < minor_version:
                    previous_tags.append(tag)
            except ValueError:
                continue

        old_tag = max(previous_tags, key=parse_to_semver) if previous_tags else None
        updates.append(ImageUpdate(
            image_name=name,
            old_tag=old_tag,
            new_tag=minor_tag,
            update_type="minor",
        ))

    return updates, new_entries


def update_target_versions(all_image_entries: list[ImageEntry]) -> None:
    """
    Update targetVersion fields for all image entries of a component.

    The highest version gets ">= X.Y" format and all others get "X.Y.x" format.
    Only processes entries with valid semantic versions.

    Args:
        all_image_entries: All image entries for a component (existing + new)
    """
    # Filter to entries with parseable semantic versions
    parseable_entries = []
    for entry in all_image_entries:
        try:
            parse_to_semver(entry.tag)
            parseable_entries.append(entry)
        except ValueError:
            continue

    if not parseable_entries:
        return

    # Find the highest version entry
    highest_entry = max(
        parseable_entries,
        key=lambda img: parse_to_semver(img.tag),
    )
    highest_version = parse_to_semver(highest_entry.tag)

    # Set all entries to "X.Y.x" format first
    for entry in parseable_entries:
        version = parse_to_semver(entry.tag)
        entry.update_target_version(f"{version.major}.{version.minor}.x")

    # Set the highest one to ">= X.Y" format
    highest_entry.update_target_version(f">= {highest_version.major}.{highest_version.minor}")


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

    # Update targetVersion fields for all entries (existing + new)
    all_image_entries = image_list + new_entries
    update_target_versions(all_image_entries)

    return all_updates, new_entries


def update_images_data(
    images_data: ImagesData,
    new_versions_by_name: dict[str, dict[str, list[str]]]
) -> list[ImageUpdate]:
    """
    Update the ImagesData structure with new versions and return structured update info.

    Main function that processes all images and applies updates according to their
    type (singleton vs versioned). It modifies the images_data in-place
    and returns a comprehensive list of all changes made.

    Args:
        images_data: The ImagesData object loaded from images.yaml
        new_versions_by_name: Maps image name to its new 'patch' and 'minor' versions

    Returns:
        List of structured ImageUpdate objects detailing each update performed
    """
    all_updates: list[ImageUpdate] = []
    all_new_entries: list[ImageEntry] = []

    # Group images by name for processing
    images_by_name = images_data.get_images_by_name()

    # Process each image group that has new versions available
    for name, image_list in images_by_name.items():
        if name not in new_versions_by_name:
            continue

        greater = new_versions_by_name[name]
        has_target_version = any(img.has_target_version() for img in image_list)

        if not has_target_version:
            # Handle singleton images (no targetVersion)
            all_greater_tags = greater["patch"] + greater["minor"]
            updates = update_singleton_image(image_list, all_greater_tags, name)
            all_updates.extend(updates)
        else:
            # Handle versioned images (with targetVersion)
            updates, new_entries = update_versioned_images(image_list, greater, name)
            all_updates.extend(updates)
            all_new_entries.extend(new_entries)

    # Add all new entries to the main data structure
    images_data.add_images(all_new_entries)
    return all_updates


# --- I/O and Formatting Functions ---
def write_yaml_file(
        data: ImagesData,
        filename: str
) -> None:
    """
    Write the ImagesData to a YAML file using standard YAML formatting.

    Args:
        data: The ImagesData object to be written
        filename: The path to the output YAML file
    """
    with open(filename, "w") as f:
        yaml.safe_dump(
            data.to_dict(),
            f,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
            width=float('inf'),
        )


def create_release_notes(
    updates: list[ImageUpdate],
    images_data: ImagesData,
    all_available_tags: dict[str, list[str]],
    filename: str,
) -> None:
    """
    Create a markdown file with links to release notes of all added or changed releases.

    Generates comprehensive release notes including intermediate versions to ensure
    no breaking changes are missed during updates. Links directly to GitHub release pages.

    Args:
        updates: A list of structured update objects
        images_data: The updated ImagesData (used to find source repositories)
        all_available_tags: A map of image names to all their available tags
        filename: The path to the output release notes markdown file
    """
    if not updates:
        return

    repos_by_name = {
        img.name: img.sourceRepository
        for img in images_data.images
        if img.sourceRepository
    }

    updates_by_image: dict[str, list[str]] = defaultdict(list)
    for update in updates:
        image_name = update.image_name
        old_tag = update.old_tag
        new_tag = update.new_tag

        if old_tag:
            try:
                valid_tags = []
                for t in all_available_tags[image_name]:
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
                updates_by_image[image_name].extend(intermediate)
            except ValueError as e:
                print(f"Error: Could not determine upgrade path for {image_name} ")
                print(f" with update {update}: {e}", file=sys.stderr)
                sys.exit(1)
        else:
            updates_by_image[image_name].append(new_tag)

    with open(filename, "w") as f:
        f.write("# Release Notes\n\n")
        f.write(
            "The following images have been updated. Please review the release notes for each "
            "component to check if changes need to be made to our Helm charts:\n\n"
        )
        f.write(
            "**Note**: All intermediate versions between the current and new version are listed "
            "to ensure no breaking changes are missed.\n\n"
        )

        for image_name in sorted(updates_by_image.keys()):
            f.write(f"## {image_name}\n\n")
            repo_url = repos_by_name.get(image_name)

            unique_tags = sorted(list(set(updates_by_image[image_name])), key=parse_to_semver)

            for tag in unique_tags:
                if repo_url:
                    release_link = f"https://{repo_url}/releases/tag/{tag}"
                    f.write(f"- [{tag}]({release_link})\n")
                else:
                    f.write(f"- {tag}\n")
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

    try:
        images_data = load_and_validate_images_data(args.images_yaml_path)
    except (FileNotFoundError, yaml.YAMLError, ValueError) as e:
        print(f"Error loading images data: {e}", file=sys.stderr)
        sys.exit(1)

    oci_api = oci_client.client_with_dockerauth()

    image_groups: dict[tuple[str, str], list[ImageEntry]] = defaultdict(list)
    for image in images_data.images:
        image_groups[(image.name, image.repository)].append(image)

    all_new_versions: dict[str, dict[str, list[str]]] = {}
    all_available_tags: dict[str, list[str]] = {}

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

        all_available_tags[name] = available_tags
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
            all_new_versions[name] = greater_versions

    if all_new_versions:
        updates = update_images_data(images_data, all_new_versions)

        if updates:
            images_data.sort_images()
            write_yaml_file(images_data, args.images_yaml_path)
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
