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

import semver
import pytest

import version


def test_find_latest_version():
    versions = (semver.VersionInfo.parse(v) for v in (
            '0.0.10',
            '0.20.1',
            '2.50.100',
            '3.0.1',
            '1.0.0',
    ))
    result = version.find_latest_version(versions)
    assert str(result) == '3.0.1'


def test_ignore_prerelease_versions():
    versions = (semver.VersionInfo.parse(v) for v in (
            '0.0.10',
            '3.0.1',
            '1.0.0',
            '3.1.0-foo-bar',
    ))
    result = version.find_latest_version(versions, ignore_prerelease_versions=True)
    assert str(result) == '3.0.1'


def test_argument_validation():
    with pytest.raises(ValueError):
        version.process_version(version_str='invalid', operation='noop')

    # prerelease arg missing
    with pytest.raises(ValueError):
        version.process_version(version_str='1.2.3', operation='set_prerelease')

    # build_metadata_missing
    with pytest.raises(ValueError):
        version.process_version(version_str='1.2.3', operation='set_build_metadata')

    # metadata-len-smaller-than-zero
    with pytest.raises(ValueError):
        version.process_version(
        version_str='3.5.4',
        operation='set_build_metadata',
        build_metadata='someRandomString',
        build_metadata_length=-1
    )

    # prerelease present (in version) if using append_prerelease
    with pytest.raises(ValueError):
        version.process_version(
        version_str='3.5.4-foo',
        operation='append_prerelease'
    )

    # prerelease missing if using append_prerelease
    with pytest.raises(ValueError):
        version.process_version(
        version_str='3.5.4',
        operation='append_prerelease',
        prerelease='foo'
    )

    # version_str missing if using "set_verbatim"
    with pytest.raises(ValueError):
        version.process_version(
            version_str='3.1.4-foo',
            operation='set_verbatim',
            prerelease='baz'
        )


def test_noop():
    parsed = version.process_version(version_str='1.2.3-abc', operation='noop')
    assert parsed == '1.2.3-abc'


def test_set_build_metadata_length():
    parsed = version.process_version(
        version_str='1.3.5',
        operation='set_build_metadata',
        build_metadata='someRandomString',
        build_metadata_length=10
    )
    assert len(parsed.removeprefix('1.3.5+')) == 10


def test_set_prerelease_without_suffix():
    parsed = version.process_version(
        version_str='1.2.3',
        operation='set_prerelease',
        prerelease='dev'
    )
    assert parsed == '1.2.3-dev'


def test_set_build_metadata_without_suffix():
    parsed = version.process_version(
        version_str='3.3.3',
        operation='set_build_metadata',
        build_metadata='build'
    )
    assert parsed == '3.3.3+build'


def test_set_prerelease_with_prerelease():
    parsed = version.process_version(
        version_str='1.2.3-foo',
        operation='set_prerelease',
        prerelease='dev'
    )
    assert parsed == '1.2.3-dev'


def test_set_build_metadata_with_prerelease():
    parsed = version.process_version(
        version_str='3.3.3-foo',
        operation='set_build_metadata',
        build_metadata='build'
    )
    assert parsed == '3.3.3+build'


def test_set_prerelease_with_build_metadata():
    parsed = version.process_version(
        version_str='1.2.3+foo',
        operation='set_prerelease',
        prerelease='dev'
    )
    assert parsed == '1.2.3-dev'


def test_set_build_metadata_with_build_metadata():
    parsed = version.process_version(
        version_str='3.3.3+foo',
        operation='set_build_metadata',
        build_metadata='build'
    )
    assert parsed == '3.3.3+build'


def test_append_prerelease():
    parsed = version.process_version(
        version_str='4.9.16-foo',
        operation='append_prerelease',
        prerelease='bar',
    )
    assert parsed == '4.9.16-foo-bar'


def test_set_verbatim_with_verbatim_version():
    parsed = version.process_version(
        version_str='3.1.4-foo+bar',
        operation='set_verbatim',
        verbatim_version='master',
    )
    assert parsed == 'master'


def test_bumping():
    # major
    parsed = version.process_version(version_str='2.4.6', operation='bump_major')
    assert parsed == '3.0.0'

    # minor
    parsed = version.process_version(version_str='2.4.6', operation='bump_minor')
    assert parsed == '2.5.0'

    # patch
    parsed = version.process_version(version_str='2.4.6', operation='bump_patch')
    assert parsed  == '2.4.7'


def test_smallest_versions():
    # no filtering if keep >= amount of versions
    assert set(version.smallest_versions({'1.2.3', '2.3.4'}, keep=2)) == set()
    assert set(version.smallest_versions({'1.2.3', '2.3.4'}, keep=10)) == set()

    # keep greatest (returned versions are intended to be removed)
    assert set(version.smallest_versions({'1.2.3', '2.3.4', '3.0'}, keep=1)) == {'1.2.3', '2.3.4'}
