# Copyright (c) 2019 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
# under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import collections
import dataclasses
import semver

from typing import (
    Iterable,
    Set,
)

import ci.util

NOOP = 'noop'
SET_PRERELEASE = 'set_prerelease'
APPEND_PRERELEASE = 'append_prerelease'
SET_BUILD_METADATA = 'set_build_metadata'
SET_PRERELEASE_AND_BUILD = 'set_prerelease_and_build'
SET_VERBATIM = 'set_verbatim'


@dataclasses.dataclass
class _VersionMetadata:
    prefix: str = None


def parse_to_semver(
    version: str,
):
    '''
    parses the given version into a semver.VersionInfo object.

    Different from strict semver, the given version is preprocessed, if required, to
    convert the version into a valid semver version, if possible.

    The following preprocessings are done:

    - strip away `v` prefix
    - append patch-level `.0` for two-digit versions
    '''
    semver_version_info, _ = _parse_to_semver_and_metadata(version)
    return semver_version_info


def _parse_to_semver_and_metadata(version: str):
    def raise_invalid():
        raise ValueError(f'not a valid (semver) version: {version}')

    if not version:
        raise_invalid()

    semver_version = version
    metadata = _VersionMetadata()

    # strip leading `v`
    if version[0] == 'v':
        semver_version = version[1:]
        metadata.prefix='v'

    # in most cases, we should be fine now
    try:
        return semver.parse_version_info(semver_version), metadata
    except ValueError:
        pass # try extending `.0` as patch-level

    # blindly append patch-level
    if '-' in version:
        sep = '-'
    else:
        sep = '+'

    numeric, sep, suffix = semver_version.partition(sep)
    numeric += '.0'

    return semver.parse_version_info(numeric + sep + suffix), metadata


def process_version(
    version_str: str,
    operation: str,
    prerelease: str=None,
    build_metadata: str=None,
    # Limit the length of the build-metadata suffix.
    # By default we use 12 chars, following the advice given in
    # https://blog.cuviper.com/2013/11/10/how-short-can-git-abbreviate/
    # as we usually use git commit hashes
    build_metadata_length: int=12,
    verbatim_version: str=None,
):
    if operation in [SET_PRERELEASE,SET_PRERELEASE_AND_BUILD,APPEND_PRERELEASE] and not prerelease:
        raise ValueError('Prerelease must be given when replacing or appending.')
    if operation in [SET_BUILD_METADATA,SET_PRERELEASE_AND_BUILD]:
        if not build_metadata:
            raise ValueError('Build metadata must be given when replacing.')
        if build_metadata_length < 0:
            raise ValueError('Build metadata must not be empty')
    if operation == SET_VERBATIM and (not verbatim_version or prerelease or build_metadata):
        raise ValueError('Exactly verbatim-version must be given when using operation set_verbatim')

    parsed_version, metadata = _parse_to_semver_and_metadata(version_str)
    version_str = str(parsed_version)

    if operation == APPEND_PRERELEASE and not parsed_version.prerelease:
        raise ValueError('Given SemVer must have prerelease-version to append to.')

    if hasattr(semver, operation):
        function = getattr(semver, operation)
        processed_version = function(version_str)
    elif operation == NOOP:
        processed_version = version_str
    elif operation == SET_VERBATIM:
        processed_version = str(verbatim_version)
    elif operation == APPEND_PRERELEASE:
        parsed_version._prerelease = '-'.join((parsed_version.prerelease, prerelease))
        processed_version = str(parsed_version)
    else:
        parsed_version._prerelease = None
        parsed_version._build = None
        if operation in [SET_PRERELEASE, SET_PRERELEASE_AND_BUILD]:
            parsed_version._prerelease = prerelease
        if operation in [SET_BUILD_METADATA, SET_PRERELEASE_AND_BUILD]:
            parsed_version._build = build_metadata[:build_metadata_length]
        processed_version = str(parsed_version)

    if metadata.prefix:
        return metadata.prefix + process_version
    return processed_version


def find_latest_version(versions):
    latest_candidate = None

    for candidate in versions:
        if not latest_candidate:
            latest_candidate = candidate
            continue
        if candidate > latest_candidate:
            latest_candidate = candidate
    return latest_candidate


def find_latest_version_with_matching_major(reference_version: semver.VersionInfo, versions):
    latest_candidate = None
    for candidate in versions:
        # skip if major version does not match
        if candidate.major != reference_version.major:
            continue
        if candidate > reference_version:
            if not latest_candidate or latest_candidate < candidate:
                latest_candidate = candidate
    return latest_candidate


def partition_by_major_and_minor(
    versions: Iterable[semver.VersionInfo],
) -> Iterable[Set[semver.VersionInfo]]:
    '''partition an iterable of semver VersionInfos by their joined major and minor version
    '''
    partitions = collections.defaultdict(set)
    for version_info in versions:
        partitions[(version_info.major,version_info.minor)].add(version_info)
    yield from [
        sorted(partition, reverse=True)
        for partition in partitions.values()
    ]


def is_semver_parseable(version_string: str):
    try:
        parse_to_semver(version_string)
    except ValueError:
        ci.util.verbose(f"Could not parse '{version_string}' as semver version")
        return False
    return True
