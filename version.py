# Copyright (c) 2019-2020 SAP SE or an SAP affiliate company. All rights reserved. This file is
# licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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
    Union,
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
    version,
):
    '''
    parses the given version into a semver.VersionInfo object.

    Different from strict semver, the given version is preprocessed, if required, to
    convert the version into a valid semver version, if possible.

    The following preprocessings are done:

    - strip away `v` prefix
    - append patch-level `.0` for two-digit versions

    @param version: either a str, or a product.model object with a `version` attr
    '''
    if isinstance(version, str):
        version_str = version
    else:
        if hasattr(version, 'version'):
            if callable(version.version):
                version_str = version.version()
            else:
                version_str = str(version.version)
        elif version is None:
            raise ValueError('version must not be None')
        else:
            ci.util.warning(f'unexpected type for version: {type(version)}')
            version_str = str(version) # fallback

    semver_version_info, _ = _parse_to_semver_and_metadata(version_str)
    return semver_version_info


def _parse_to_semver_and_metadata(version: str):
    def raise_invalid():
        raise ValueError(f'not a valid (semver) version: `{version}`')

    if not version:
        raise_invalid()

    semver_version = version
    metadata = _VersionMetadata()

    # strip leading `v`
    if version[0] == 'v':
        semver_version = version[1:]
        metadata.prefix = 'v'

    # in most cases, we should be fine now
    try:
        return semver.VersionInfo.parse(semver_version), metadata
    except ValueError:
        pass # try extending `.0` as patch-level

    # blindly append patch-level
    if '-' in version:
        sep = '-'
    else:
        sep = '+'

    numeric, sep, suffix = semver_version.partition(sep)
    numeric += '.0'

    try:
        return semver.VersionInfo.parse(numeric + sep + suffix), metadata
    except ValueError:
        # re-raise with original version str
        raise_invalid()


def _sort_versions(
    versions
):
    '''
    sorts the given versions (which may be a sequence containing any combination of
    str, semver.VersionInfo, or model element bearing a `version` attr) on a best-effort
    base.
    Firstly, it is checked whether all versions are semver-parsable, using this module's
    `parse_to_semver` (which allows some deviations from strict semver-v2). If all versions
    are parsable, str representations of the originally given versions are returned, ordered
    according to semver artithmetics.
    Otherwise, sorting falls back to alphabetical sorting as implemented by python's str.

    Note that there is _no_ validation of any kind w.r.t. to the sanity of the passed values.

    This function is not intended to be used by external users, and is planned to be removed
    again.
    '''
    if not versions:
        return

    def to_ver(version_obj):
        if hasattr(version_obj, 'version'):
            if callable(version_obj.version):
                return version_obj.version()
            else:
                return version_obj.version
        else:
            return str(version_obj)

    try:
        # try if all versions are semver-compatible
        for version_str in map(to_ver, versions):
            parse_to_semver(version_str)

        return sorted(
            versions,
            key=lambda vo: parse_to_semver(to_ver(vo)),
        )
    except ValueError:
        pass # ignore and fall-back to str-sorting

    return sorted(
        versions,
        key=to_ver,
    )


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
        function = getattr(parsed_version, operation)
        processed_version = str(function())
    elif operation == NOOP:
        processed_version = version_str
    elif operation == SET_VERBATIM:
        processed_version = str(verbatim_version)
    elif operation == APPEND_PRERELEASE:
        parsed_version = parsed_version.replace(
            prerelease='-'.join((parsed_version.prerelease, prerelease))
        )
        processed_version = str(parsed_version)
    else:
        parsed_version = parsed_version.replace(prerelease=None, build=None)
        if operation in [SET_PRERELEASE, SET_PRERELEASE_AND_BUILD]:
            parsed_version = parsed_version.replace(prerelease=prerelease)
        if operation in [SET_BUILD_METADATA, SET_PRERELEASE_AND_BUILD]:
            parsed_version = parsed_version.replace(build=build_metadata[:build_metadata_length])
        processed_version = str(parsed_version)

    if metadata.prefix:
        return metadata.prefix + processed_version

    return processed_version


def find_latest_version(versions) -> str:
    latest_candidate = None
    latest_candidate_str = None

    for candidate in versions:
        if isinstance(candidate, str):
            candidate_semver = parse_to_semver(candidate)
        else:
            candidate_semver = candidate

        if not latest_candidate:
            latest_candidate = candidate_semver
            latest_candidate_str = candidate
            continue
        if candidate_semver > latest_candidate:
            latest_candidate = candidate_semver
            latest_candidate_str = candidate

    return latest_candidate_str


def find_latest_version_with_matching_major(reference_version: semver.VersionInfo, versions):
    latest_candidate_semver = None
    latest_candidate_str = None

    if isinstance(reference_version, str):
        reference_version = parse_to_semver(reference_version)

    for candidate in versions:
        if isinstance(candidate, str):
            candidate_semver = parse_to_semver(candidate)
        else:
            candidate_semver = candidate

        # skip if major version does not match
        if candidate_semver.major != reference_version.major:
            continue
        if candidate_semver > reference_version:
            if not latest_candidate_semver or latest_candidate_semver < candidate_semver:
                latest_candidate_semver = candidate_semver
                latest_candidate_str = candidate

    return latest_candidate_str


def find_latest_version_with_matching_minor(
    reference_version: Union[semver.VersionInfo, str],
    versions,
) -> str:
    latest_candidate_semver = None
    latest_candidate_str = None

    if isinstance(reference_version, str):
        reference_version = parse_to_semver(reference_version)

    for candidate in versions:
        if isinstance(candidate, str):
            candidate_semver = parse_to_semver(candidate)
        else:
            candidate_semver = candidate

        # skip if major version does not match
        if candidate_semver.major != reference_version.major:
            continue
        # skip if minor version does not match
        if candidate_semver.minor != reference_version.minor:
            continue

        if candidate_semver >= reference_version:
            if not latest_candidate_semver or latest_candidate_semver < candidate_semver:
                latest_candidate_semver = candidate_semver
                latest_candidate_str = candidate

    return latest_candidate_str


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
