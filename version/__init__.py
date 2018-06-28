# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

import os
import select
import semver
import sys

own_dir = os.path.dirname(os.path.realpath(__file__))
sys.path.append(os.path.join(own_dir, os.path.pardir))

SEMVER_OPS = set([
    'bump_minor',
    'bump_major',
    'bump_patch',
    'finalize_version'
])

NOOP = 'noop'
SET_PRERELEASE = 'set_prerelease'
APPEND_PRERELEASE = 'append_prerelease'
SET_BUILD_METADATA = 'set_build_metadata'
SET_PRERELEASE_AND_BUILD = 'set_prerelease_and_build'

CUSTOM_OPS = set([
    NOOP,
    SET_PRERELEASE,
    APPEND_PRERELEASE,
    SET_BUILD_METADATA,
    SET_PRERELEASE_AND_BUILD
])

ALL_OPS = SEMVER_OPS.union(CUSTOM_OPS)


def process_version(
    version_str: str,
    operation: str,
    prerelease: str=None,
    build_metadata: str=None,
    # Limit the length of the build-metadata suffix.
    # By default we use 12 chars, following the advice given in
    # https://blog.cuviper.com/2013/11/10/how-short-can-git-abbreviate/
    # as we usually use git commit hashes
    build_metadata_length: int=12
    ):
    if operation in [SET_PRERELEASE,SET_PRERELEASE_AND_BUILD,APPEND_PRERELEASE] and not prerelease:
        raise ValueError('Prerelease must be given when replacing or appending.')
    if operation in [SET_BUILD_METADATA,SET_PRERELEASE_AND_BUILD]:
        if not build_metadata:
            raise ValueError('Build metadata must be given when replacing.')
        if build_metadata_length < 0:
            raise ValueError('Build metadata length must be >= 0')

    parsed_version = dict(semver.parse(version_str))

    if operation == APPEND_PRERELEASE and not parsed_version['prerelease']:
        raise ValueError('Given SemVer must have prerelease-version to append to.')

    if hasattr(semver, operation):
        function = getattr(semver, operation)
        processed_version = function(version_str)
    elif operation == NOOP:
        processed_version = version_str
    elif operation == APPEND_PRERELEASE:
        parsed_version['prerelease'] += "-" + prerelease
        processed_version = semver.format_version(**parsed_version)
    else:
        parsed_version['prerelease'] = None
        parsed_version['build'] = None
        if operation in [SET_PRERELEASE, SET_PRERELEASE_AND_BUILD]:
            parsed_version['prerelease'] = prerelease
        if operation in [SET_BUILD_METADATA, SET_PRERELEASE_AND_BUILD]:
            parsed_version['build'] = build_metadata[:build_metadata_length]
        processed_version = semver.format_version(**parsed_version)

    return processed_version


def find_latest_version_with_matching_major(reference_version: semver.VersionInfo, versions):
    latest_candidate = None
    for candidate in versions:
        # skip if major version does not match
        if candidate.major != reference_version.major: continue
        if candidate > reference_version:
            if not latest_candidate or latest_candidate < candidate:
                latest_candidate = candidate
    return latest_candidate
