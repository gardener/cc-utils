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
3.  It uses the `regctl` command-line tool to query container registries for new tags.
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
import subprocess  # nosec: B404
import yaml
import re
import sys
from typing import Dict, List, Tuple, Optional, TypedDict
from collections import defaultdict


# --- Type Definitions ---
class Update(TypedDict):
    """A structured representation of a single image update."""

    image_name: str
    old_tag: Optional[str]
    new_tag: str
    update_type: str  # 'patch', 'minor', or 'singleton'


# --- Helper Functions ---
def check_regctl_available() -> bool:
    """Check if regctl command is available."""
    try:
        subprocess.run(["regctl", "--help"], capture_output=True, check=True)  # nosec: B603, B607
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def run_regctl_command(repository: str) -> List[str]:
    """
    Run `regctl tag ls` command and return a list of tags.

    Args:
        repository (str): The image repository to query.

    Returns:
        List[str]: A list of tag strings, e.g., ['v1.2.3', 'v1.2.4'].
                   Returns an empty list on error.
    """
    try:
        result = subprocess.run(  # nosec: B603, B607
            ["regctl", "tag", "ls", repository],
            capture_output=True,
            text=True,
            check=True,
        )
        return [tag.strip() for tag in result.stdout.strip().split("\n") if tag.strip()]
    except subprocess.CalledProcessError as e:
        print(f"Error running regctl for {repository}: {e}", file=sys.stderr)
        return []


def parse_version(tag: str) -> Optional[Tuple[int, int, int]]:
    """
    Parse a semantic version from a tag string (vX.Y.Z).

    Args:
        tag (str): A tag string, e.g., 'v1.2.3'.

    Returns:
        Optional[Tuple[int, int, int]]: A tuple of (major, minor, patch) integers, otherwise None.
    """
    match = re.match(r"^v(\d+)\.(\d+)\.(\d+)$", tag)
    if match:
        return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return None


def get_clean_tag(tag: str) -> Optional[str]:
    """
    Return the tag if it's a clean semantic version (vX.Y.Z), otherwise None.
    This filters out tags like 'latest', 'v1.2.3-rc1', or 'v1.2.3-amd64'.

    Args:
        tag (str): A tag string.

    Returns:
        Optional[str]: The tag if it's in the 'vX.Y.Z' format, otherwise None.
    """
    if re.match(r"^v\d+\.\d+\.\d+$", tag):
        return tag
    return None


def filter_valid_tags(tags: List[str]) -> List[str]:
    """
    Filter a list of tags to include only valid 'vX.Y.Z' semantic version tags.

    Args:
        tags (List[str]): A list of tag strings from the registry.

    Returns:
        List[str]: A filtered list of valid tags.
    """
    return [tag for tag in tags if get_clean_tag(tag)]


# --- Core Logic Functions ---
def find_newer_versions(current_tags: List[str], available_tags: List[str]) -> Dict[str, List[str]]:
    """
    Compare current tags with available tags to find newer patch and minor/major versions.

    Args:
        current_tags (List[str]): A list of current tag strings from images.yaml.
        available_tags (List[str]): A list of all available tag strings from the registry.

    Returns:
        Dict[str, List[str]]: A dictionary with 'patch' and 'minor' keys
        containing lists of new tags.
    """
    available_tags = filter_valid_tags(available_tags)

    current_versions = sorted(
        [(parsed, tag) for tag in current_tags if (parsed := parse_version(tag))],
        key=lambda x: x[0],
    )

    if not current_versions:
        # If there are no current versions, all available tags are considered new 'minor' versions.
        return {"patch": [], "minor": sorted(available_tags, key=parse_version)}

    available_versions = sorted(
        [(parsed, tag) for tag in available_tags if (parsed := parse_version(tag))],
        key=lambda x: x[0],
    )

    newer_major_or_minor = []
    newer_patch = []

    highest_current = max(current_versions, key=lambda x: x[0])[0]

    current_minor_versions = defaultdict(list)
    for (major, minor, patch), tag in current_versions:
        current_minor_versions[(major, minor)].append((patch, tag))

    for (major, minor, patch), tag in available_versions:
        if (major, minor) in current_minor_versions:
            max_current_patch = max(p for p, _ in current_minor_versions[(major, minor)])
            if patch > max_current_patch:
                newer_patch.append(tag)
        elif (major, minor, patch) > highest_current:
            newer_major_or_minor.append(tag)

    return {"patch": newer_patch, "minor": newer_major_or_minor}


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
    for i, image in enumerate(images_data["images"]):
        images_by_name[image["name"]].append((i, image))

    new_image_entries = []
    indices_to_remove = set()

    for name, image_list in images_by_name.items():
        if name not in new_versions_by_name:
            continue

        newer = new_versions_by_name[name]
        has_target_version = any("targetVersion" in img for _, img in image_list)

        if not has_target_version:
            # Handle images without 'targetVersion' (singleton components)
            all_newer_tags = newer["patch"] + newer["minor"]
            if all_newer_tags:
                latest_tag = max(all_newer_tags, key=parse_version)
                old_tag = image_list[0][1]["tag"]

                for idx, _ in image_list:
                    indices_to_remove.add(idx)

                template_image = image_list[0][1].copy()
                template_image["tag"] = latest_tag
                new_image_entries.append(template_image)
                updates.append(
                    {
                        "image_name": name,
                        "old_tag": old_tag,
                        "new_tag": latest_tag,
                        "update_type": "singleton",
                    }
                )
        else:
            # Handle images with 'targetVersion'

            # Update patch versions
            for patch_tag in newer["patch"]:
                patch_version = parse_version(patch_tag)
                if not patch_version:
                    continue

                major, minor, _ = patch_version

                # Find the image entry for this minor version to update its tag
                for img in image_list:
                    img_version = parse_version(img[1]["tag"])
                    if img_version and (img_version[0], img_version[1]) == (
                        major,
                        minor,
                    ):
                        old_tag = img[1]["tag"]
                        img[1]["tag"] = patch_tag
                        updates.append(
                            {
                                "image_name": name,
                                "old_tag": old_tag,
                                "new_tag": patch_tag,
                                "update_type": "patch",
                            }
                        )
                        break

            # Add new minor versions
            for minor_tag in newer["minor"]:
                template_image = image_list[0][1].copy()
                template_image["tag"] = minor_tag
                template_image["targetVersion"] = (
                    f"{parse_version(minor_tag)[0]}.{parse_version(minor_tag)[1]}.x"
                )
                new_image_entries.append(template_image)

                # Find the previous highest version to set as 'old_tag'
                all_tags = [img["tag"] for _, img in image_list] + [
                    t["tag"] for t in new_image_entries
                ]
                previous_tags = [t for t in all_tags if parse_version(t) < parse_version(minor_tag)]
                old_tag = max(previous_tags, key=parse_version) if previous_tags else None
                updates.append(
                    {
                        "image_name": name,
                        "old_tag": old_tag,
                        "new_tag": minor_tag,
                        "update_type": "minor",
                    }
                )

            # Consolidate all images for this name (original, updated, and new)
            all_image_entries_for_name = [img for _, img in image_list] + new_image_entries

            if all_image_entries_for_name:
                # Find the highest version and set its targetVersion to ">= X.Y"
                highest_entry = max(
                    all_image_entries_for_name,
                    key=lambda img: parse_version(img["tag"]),
                )
                highest_version = parse_version(highest_entry["tag"])

                # Reset all others to "X.Y.x" format
                for entry in all_image_entries_for_name:
                    ver = parse_version(entry["tag"])
                    entry["targetVersion"] = f"{ver[0]}.{ver[1]}.x"

                # Set the highest one to ">= X.Y"
                highest_entry["targetVersion"] = f">= {highest_version[0]}.{highest_version[1]}"

    # Apply changes to the main data structure
    if indices_to_remove:
        images_data["images"] = [
            img for i, img in enumerate(images_data["images"]) if i not in indices_to_remove
        ]

    images_data["images"].extend(new_image_entries)
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
        List[str]: A sorted list of tags between current_tag and new_tag.
    """
    current_version = parse_version(current_tag)
    new_version = parse_version(new_tag)

    if not current_version or not new_version:
        return [new_tag]

    intermediate_tags = []
    for tag in available_tags:
        ver = parse_version(tag)
        if ver and current_version < ver <= new_version:
            intermediate_tags.append(tag)

    return sorted(intermediate_tags, key=parse_version)


# --- I/O and Formatting Functions ---
def sort_images_by_name(images_data: Dict):
    """Sorts the list of images in place by name and then by version."""
    images_data["images"].sort(key=lambda x: (x["name"], parse_version(x["tag"])))


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
):
    """
    Create a markdown file with links to release notes of all added or changed
    releases, including intermediate releases.

    Args:
        updates (List[Update]): A list of structured update objects.
        images_data (Dict): The updated images.yaml data (used to find source repositories).
        all_available_tags (Dict[str, List[str]]): A map of image names to all their available tags.
        filename (str): The path to the output release notes markdown file.
    """
    if not updates:
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

            unique_tags = sorted(list(set(updates_by_image[image_name])), key=parse_version)

            for tag in unique_tags:
                if repo_url:
                    release_link = f"https://{repo_url}/releases/tag/{tag}"
                    f.write(f"- [{tag}]({release_link})\n")
                else:
                    f.write(f"- {tag}\n")
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

    if not check_regctl_available():
        print("Error: regctl command not found.", file=sys.stderr)
        print(
            "Please install regctl from https://github.com/regclient/regclient",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        with open(images_yaml_path, "r") as f:
            images_data = yaml.safe_load(f)
    except (FileNotFoundError, yaml.YAMLError) as e:
        print(f"Error reading or parsing {images_yaml_path}: {e}", file=sys.stderr)
        sys.exit(1)

    image_groups = defaultdict(list)
    for image in images_data["images"]:
        image_groups[(image["name"], image["repository"])].append(image)

    all_new_versions = {}
    all_available_tags = {}

    for (name, repository), image_list in image_groups.items():
        print(f"Checking {name} at {repository}...", file=sys.stderr)

        available_tags = run_regctl_command(repository)
        if not available_tags:
            print(f"No tags found for {repository}", file=sys.stderr)
            continue

        all_available_tags[name] = available_tags
        current_tags = [img["tag"] for img in image_list]
        newer_versions = find_newer_versions(current_tags, available_tags)

        if newer_versions["patch"] or newer_versions["minor"]:
            print(f"New tags found for {name}:", file=sys.stderr)
            if newer_versions["patch"]:
                print(f"  Patch updates: {', '.join(newer_versions['patch'])}", file=sys.stderr)
            if newer_versions["minor"]:
                print(
                    f"  Minor/Major updates: {', '.join(newer_versions['minor'])}", file=sys.stderr
                )
            all_new_versions[name] = newer_versions

    if all_new_versions:
        updates = update_images_data(images_data, all_new_versions)
        sort_images_by_name(images_data)

        write_yaml_with_formatting(images_data, images_yaml_path)
        print(f"\nUpdated {images_yaml_path} with {len(updates)} changes.", file=sys.stderr)

        create_release_notes(updates, images_data, all_available_tags, release_notes_path)
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
