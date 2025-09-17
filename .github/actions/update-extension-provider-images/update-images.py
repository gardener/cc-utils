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
Image Updater Script.
"""

import argparse
import yaml
import re
import sys
import semver
import time
from typing import Dict, List, Tuple, Optional, TypedDict
from collections import defaultdict
from oci import client as oci_client
from oci.model import OciImageNotFoundException
from version import parse_to_semver, is_final


# --- Type Definitions ---
class Update(TypedDict):
    """A structured representation of a single image update."""

    image_name: str
    old_tag: Optional[str]
    new_tag: str
    update_type: str  # 'patch', 'minor', or 'singleton'


# --- Helper Functions ---
def get_tags_from_registry(oci_client: oci_client.Client, repository: str) -> List[str]:
    """
    Use the oci.client to retrieve a list of tags for a repository.

    Args:
        oci_client (oci_client.Client): An initialized OCI client.
        repository (str): The image repository to query.

    Returns:
        List[str]: A list of tag strings, e.g., ['v1.2.3', 'v1.2.4'].
                   Returns an empty list on error.
    """
    try:
        return oci_client.tags(image_reference=repository)
    except Exception as e:
        print(f"Error retrieving tags for {repository}: {e}", file=sys.stderr)
        return []


def image_exists(oci_client: oci_client.Client, repository: str, tag: str) -> bool:
    """
    Check if a specific image tag exists in the registry by trying to fetch its manifest.

    Args:
        oci_client (oci_client.Client): An initialized OCI client.
        repository (str): The image repository.
        tag (str): The image tag.

    Returns:
        bool: True if the image exists, False otherwise.
    """
    image_ref = f"{repository}:{tag}"
    retries = 3
    delay = 2  # seconds

    for i in range(retries):
        try:
            oci_client.manifest(image_reference=image_ref, accept="*/*")
            return True
        except OciImageNotFoundException:
            print(f"  ✗ Manifest for {image_ref} not found.", file=sys.stderr)
            return False
        except Exception as e:
            # Check if the error is a rate-limiting error
            if "429" in str(e) and "Too Many Requests" in str(e):
                if i < retries - 1:
                    print(
                        f"  ! Rate limited on {image_ref}. Retrying in {delay}s...", file=sys.stderr
                    )
                    time.sleep(delay)
                    delay *= 2  # Exponential backoff
                    continue

            # For all other errors, or if retries are exhausted
            print(f"  ✗ Could not find manifest for {image_ref}", file=sys.stderr)
            print(f"    Error: {e}", file=sys.stderr)
            return False

    return False


# --- Core Logic Functions ---
def find_newer_versions(
    oci_client: oci_client.Client,
    repository: str,
    current_tags: List[str],
    available_tags: List[str],
) -> Dict[str, List[str]]:
    """
    Compare current tags with available tags to find newer patch and minor/major versions.

    Args:
        oci_client (oci_client.Client): An initialized OCI client.
        repository (str): The image repository.
        current_tags (List[str]): A list of current tag strings from images.yaml.
        available_tags (List[str]): A list of all available tag strings from the registry.

    Returns:
        Dict[str, List[str]]: A dictionary with 'patch', 'minor', and 'missing' keys
        containing lists of new tags as strings.
    """
    available_versions_map = {}
    for tag in available_tags:
        try:
            ver = parse_to_semver(tag)
            if is_final(ver):
                available_versions_map[ver] = tag
        except ValueError:
            continue

    available_versions = sorted(available_versions_map.keys())

    current_versions = []
    for tag in current_tags:
        try:
            current_versions.append(parse_to_semver(tag))
        except ValueError:
            continue
    current_versions.sort()

    if not current_versions:
        existing_minor_tags = []
        missing_minor_tags = []
        for ver in available_versions:
            tag = available_versions_map[ver]
            if image_exists(oci_client, repository, tag):
                existing_minor_tags.append(tag)
            else:
                missing_minor_tags.append(tag)
        return {
            "patch": [],
            "minor": existing_minor_tags,
            "missing": missing_minor_tags,
        }

    highest_current_ver = current_versions[-1]

    highest_patch_for_minor = defaultdict(lambda: semver.Version(0))
    for ver in current_versions:
        key = (ver.major, ver.minor)
        if ver > highest_patch_for_minor[key]:
            highest_patch_for_minor[key] = ver

    newer_patch_tags = []
    newer_minor_tags = []
    missing_tags = []

    for ver in available_versions:
        original_tag = available_versions_map[ver]
        key = (ver.major, ver.minor)

        if key in highest_patch_for_minor:
            if ver > highest_patch_for_minor[key]:
                if image_exists(oci_client, repository, original_tag):
                    newer_patch_tags.append(original_tag)
                else:
                    missing_tags.append(original_tag)
        elif ver > highest_current_ver:
            if image_exists(oci_client, repository, original_tag):
                newer_minor_tags.append(original_tag)
            else:
                missing_tags.append(original_tag)

    return {
        "patch": sorted(newer_patch_tags, key=parse_to_semver),
        "minor": sorted(newer_minor_tags, key=parse_to_semver),
        "missing": sorted(missing_tags, key=parse_to_semver),
    }


def update_images_data(images_data: Dict, new_versions_by_name: Dict[str, Dict]) -> List[Update]:
    """
    Update the in-memory images data structure with new versions and return structured update info.

    Args:
        images_data (Dict): The full data from images.yaml, loaded as a dictionary.
        new_versions_by_name (Dict[str, Dict]): Maps image name to its new
        'patch' and 'minor' versions.

    Returns:
        List[Update]: A list of structured objects detailing each update.
    """
    updates: List[Update] = []
    images_by_name = defaultdict(list)
    for image in images_data["images"]:
        images_by_name[image["name"]].append(image)

    all_new_entries_global = []

    for name, image_list in images_by_name.items():
        if name not in new_versions_by_name:
            continue

        new_entries_for_this_name = []
        newer = new_versions_by_name[name]
        has_target_version = any("targetVersion" in img for img in image_list)

        if not has_target_version:
            # Handle images without 'targetVersion' (singleton components)
            all_newer_tags = newer["patch"] + newer["minor"]
            if not all_newer_tags:
                continue

            if len(image_list) == 1:
                latest_tag = max(all_newer_tags, key=parse_to_semver)
                old_tag = image_list[0]["tag"]

                if old_tag != latest_tag:
                    image_list[0]["tag"] = latest_tag
                    updates.append(
                        {
                            "image_name": name,
                            "old_tag": old_tag,
                            "new_tag": latest_tag,
                            "update_type": "singleton",
                        }
                    )
            else:
                print(
                    f"Warning: Found {len(image_list)} entries for singleton image '{name}' "
                    f"but expected 1. Skipping update for this image.",
                    file=sys.stderr,
                )
        else:
            # Handle images with 'targetVersion'

            # Update patch versions
            for patch_tag in newer["patch"]:
                try:
                    patch_version = parse_to_semver(patch_tag)
                except ValueError:
                    continue

                for img_data in image_list:
                    try:
                        img_version = parse_to_semver(img_data["tag"])
                        if (img_version.major, img_version.minor) == (
                            patch_version.major,
                            patch_version.minor,
                        ):
                            old_tag = img_data["tag"]
                            img_data["tag"] = patch_tag
                            updates.append(
                                {
                                    "image_name": name,
                                    "old_tag": old_tag,
                                    "new_tag": patch_tag,
                                    "update_type": "patch",
                                }
                            )
                            break
                    except ValueError:
                        continue

            # Add new minor versions
            for minor_tag in newer["minor"]:
                try:
                    minor_version = parse_to_semver(minor_tag)
                except ValueError:
                    continue

                template_image = image_list[0].copy()
                template_image["tag"] = minor_tag
                template_image["targetVersion"] = f"{minor_version.major}.{minor_version.minor}.x"
                new_entries_for_this_name.append(template_image)

                all_tags = [img["tag"] for img in image_list] + [
                    t["tag"] for t in new_entries_for_this_name
                ]

                previous_tags = []
                for t in all_tags:
                    try:
                        v = parse_to_semver(t)
                        if v < minor_version:
                            previous_tags.append(t)
                    except ValueError:
                        continue

                old_tag = max(previous_tags, key=parse_to_semver) if previous_tags else None
                updates.append(
                    {
                        "image_name": name,
                        "old_tag": old_tag,
                        "new_tag": minor_tag,
                        "update_type": "minor",
                    }
                )

            # Consolidate all images for this name (original, updated, and new)
            all_image_entries_for_name = image_list + new_entries_for_this_name

            parseable_entries = []
            for entry in all_image_entries_for_name:
                try:
                    parse_to_semver(entry["tag"])
                    parseable_entries.append(entry)
                except ValueError:
                    continue

            if parseable_entries:
                highest_entry = max(
                    parseable_entries,
                    key=lambda img: parse_to_semver(img["tag"]),
                )
                highest_version = parse_to_semver(highest_entry["tag"])

                # Reset all but the highest entry to "X.Y.x" format
                for entry in parseable_entries:
                    ver = parse_to_semver(entry["tag"])
                    entry["targetVersion"] = f"{ver.major}.{ver.minor}.x"

                # Set the highest one to ">= X.Y"
                highest_entry["targetVersion"] = (
                    f">= {highest_version.major}.{highest_version.minor}"
                )

        all_new_entries_global.extend(new_entries_for_this_name)

    # Apply changes to the main data structure
    images_data["images"].extend(all_new_entries_global)
    return updates


def find_intermediate_versions(
    current_tag: str, new_tag: str, available_tags: List[str]
) -> List[str]:
    """
    Find all semantic versions between a current and a new tag.

    Args:
        current_tag (str): The starting tag (exclusive).
        new_tag (str): The ending tag (inclusive).
        available_tags (List[str]): All available tags from the registry.

    Returns:
        List[str]: A sorted list of original final version tag strings
        between current_tag and new_tag.
    """
    try:
        current_version = parse_to_semver(current_tag)
        new_version = parse_to_semver(new_tag)
    except (ValueError, TypeError):
        return [new_tag]

    intermediate_tags = []
    for tag in available_tags:
        try:
            ver = parse_to_semver(tag)
            if is_final(ver) and current_version < ver <= new_version:
                intermediate_tags.append(tag)
        except ValueError:
            continue

    return sorted(intermediate_tags, key=parse_to_semver)


# --- I/O and Formatting Functions ---
def sort_images_by_name(images_data: Dict):
    """Sorts the list of images in place by name and then by version."""

    def sort_key(image: Dict) -> Tuple[str, semver.Version]:
        """
        Generates a key for sorting images. The primary key is the image name.
        The secondary key is a semver.Version object. If a tag cannot be parsed
        as a semantic version, it is given a "zero" version to sort it consistently
        at the beginning of its group.
        """
        try:
            return (image["name"], parse_to_semver(image["tag"]))
        except ValueError:
            return (image["name"], semver.Version(0))

    images_data["images"].sort(key=sort_key)


def write_yaml_with_formatting(data: Dict, filename: str):
    """
    Write the dictionary to a YAML file using the project's style.

    Args:
        data (Dict): The final images.yaml data to be written.
        filename (str): The path to the output YAML file.
    """
    with open(filename, "w") as f:
        f.write("images:\n")
        for i, image in enumerate(data["images"]):
            f.write(f"- name: {image['name']}\n")
            f.write(f"  sourceRepository: {image['sourceRepository']}\n")

            if "resourceId" in image:
                f.write("  resourceId:\n")
                f.write(f"    name: '{image['resourceId']['name']}'\n")

            f.write(f"  repository: {image['repository']}\n")
            f.write(f'  tag: "{image["tag"]}"\n')

            if "targetVersion" in image:
                f.write(f'  targetVersion: "{image["targetVersion"]}"\n')

            if "labels" in image:
                f.write("  labels:\n")
                for label in image["labels"]:
                    f.write(f"  - name: '{label['name']}'\n")
                    f.write("    value:\n")
                    for key, value in label["value"].items():
                        formatted_value = (
                            str(value).lower() if isinstance(value, bool) else f"'{value}'"
                        )
                        f.write(f"      {key}: {formatted_value}\n")

            if i < len(data["images"]) - 1:
                f.write("\n")


def create_release_notes(
    updates: List[Update],
    images_data: Dict,
    all_available_tags: Dict[str, List[str]],
    filename: str,
    missing_images: Dict[str, List[str]],
):
    """
    Create a markdown file with links to release notes of all added or changed
    releases, including intermediate releases.

    Args:
        updates (List[Update]): A list of structured update objects.
        images_data (Dict): The updated images.yaml data (used to find source repositories).
        all_available_tags (Dict[str, List[str]]): A map of image names to all their available tags.
        filename (str): The path to the output release notes markdown file.
        missing_images (Dict[str, List[str]]): A map of image names to tags that were found
                                               but did not exist in the registry.
    """
    if not updates and not missing_images:
        return

    repos_by_name = {
        img["name"]: img["sourceRepository"]
        for img in images_data["images"]
        if "sourceRepository" in img
    }

    updates_by_image = defaultdict(list)
    for update in updates:
        image_name = update["image_name"]
        old_tag = update["old_tag"]
        new_tag = update["new_tag"]

        if old_tag:
            intermediate = find_intermediate_versions(
                old_tag, new_tag, all_available_tags[image_name]
            )
            updates_by_image[image_name].extend(intermediate)
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

    if missing_images:
        with open(filename, "a") as f:
            f.write("\n---\n\n")
            f.write("## ⚠️ Missing Images\n\n")
            f.write(
                "The following image versions were found as release tags but could not be "
                "found in their respective container registries. They have **not** been updated "
                "in `images.yaml`. This may be due to a temporary lag in the upstream build pipeline.\n\n"
            )
            for image_name in sorted(missing_images.keys()):
                f.write(f"### {image_name}\n\n")
                for tag in sorted(missing_images[image_name], key=parse_to_semver):
                    f.write(f"- `{tag}`\n")
                f.write("\n")


# --- Main Execution ---
def main():
    """Main execution function."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--images-yaml-path",
        default="imagevector/images.yaml",
        help="Path to the images.yaml file.",
    )
    parser.add_argument(
        "--release-notes-path",
        default="release-notes.md",
        help="Path where the release-notes.md file will be generated.",
    )
    args = parser.parse_args()
    images_yaml_path = args.images_yaml_path
    release_notes_path = args.release_notes_path

    try:
        with open(images_yaml_path, "r") as f:
            images_data = yaml.safe_load(f)
    except (FileNotFoundError, yaml.YAMLError) as e:
        print(f"Error reading or parsing {images_yaml_path}: {e}", file=sys.stderr)
        sys.exit(1)

    oci_api = oci_client.client_with_dockerauth()

    image_groups = defaultdict(list)
    for image in images_data["images"]:
        image_groups[(image["name"], image["repository"])].append(image)

    all_new_versions = {}
    all_available_tags = {}
    all_missing_images = {}

    for (name, repository), image_list in image_groups.items():
        print(f"Checking {name} at {repository}...", file=sys.stderr)

        available_tags = get_tags_from_registry(oci_api, repository)
        if not available_tags:
            print(f"No tags found for {repository}", file=sys.stderr)
            continue

        all_available_tags[name] = available_tags
        current_tags = [img["tag"] for img in image_list]

        newer_versions = find_newer_versions(oci_api, repository, current_tags, available_tags)

        if newer_versions.get("missing"):
            all_missing_images[name] = newer_versions["missing"]

        if newer_versions["patch"] or newer_versions["minor"]:
            print(f"New tags found for {name}:", file=sys.stderr)
            if newer_versions["patch"]:
                print(f"  Patch updates: {', '.join(newer_versions['patch'])}", file=sys.stderr)
            if newer_versions["minor"]:
                print(
                    f"  Minor/Major updates: {', '.join(newer_versions['minor'])}", file=sys.stderr
                )
            all_new_versions[name] = newer_versions

    if all_new_versions or all_missing_images:
        updates = update_images_data(images_data, all_new_versions)

        if updates:
            sort_images_by_name(images_data)
            write_yaml_with_formatting(images_data, images_yaml_path)
            print(f"\nUpdated {images_yaml_path} with {len(updates)} changes.", file=sys.stderr)
        else:
            print(f"\nNo images could be updated. See missing images below.", file=sys.stderr)

        create_release_notes(
            updates, images_data, all_available_tags, release_notes_path, all_missing_images
        )
        print(f"Created {release_notes_path}", file=sys.stderr)

        print("The following container images have been updated:")
        for update in updates:
            print(
                f"  - {update['image_name']}: {update['old_tag'] or 'N/A'} -> "
                f"{update['new_tag']} ({update['update_type']})"
            )
    else:
        print("\nNo newer versions found for any images.", file=sys.stderr)


if __name__ == "__main__":
    main()
