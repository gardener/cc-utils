# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import os
import sys

sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(
            os.path.dirname(__file__), '..', '..', '..', '.github', 'actions',
            'install-gardener-gha-libs',
        )
    ),
)

import normalise_version


def test_final_version_unchanged():
    assert normalise_version.normalise_version('1.2.3') == '1.2.3'


def test_dev_suffix_normalised():
    assert normalise_version.normalise_version('1.2.3-dev') == '1.2.3.dev0'


def test_prerelease_normalised():
    assert normalise_version.normalise_version('1.2.3-rc.1') == '1.2.3.dev0'


def test_multi_segment_prerelease_normalised():
    # bash `${version%%-*}-dev0` would give `1.2.3-foo-dev0` (wrong)
    assert normalise_version.normalise_version('1.2.3-foo-bar') == '1.2.3.dev0'


def test_read_version(tmp_path):
    (tmp_path / 'VERSION').write_text('1.5.0-dev\n')
    assert normalise_version.read_version(str(tmp_path)) == '1.5.0-dev'


def test_write_version(tmp_path):
    paths = (
        'VERSION',
        'ci/VERSION',
        'oci/VERSION',
        'ocm/VERSION',
        'cli/gardener_ci/VERSION',
    )
    for rel in paths:
        full = tmp_path / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text('old\n')

    normalise_version.write_version(str(tmp_path), '1.5.0.dev0')

    for rel in paths:
        assert (tmp_path / rel).read_text() == '1.5.0.dev0\n'
