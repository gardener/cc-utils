# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import ocm
import ocm.gardener


def test_find_upgrade_vector_newer_available():
    cid = ocm.ComponentIdentity(
        name='example.com/comp',
        version='1.0.0',
    )

    vector = ocm.gardener.find_upgrade_vector(
        component_id=cid,
        version_lookup=lambda _: ['1.0.0', '1.1.0', '2.0.0'],
    )

    assert vector is not None
    assert vector.whence.version == '1.0.0'
    assert vector.whither.version == '2.0.0'


def test_find_upgrade_vector_already_latest():
    cid = ocm.ComponentIdentity(
        name='example.com/comp',
        version='2.0.0',
    )

    vector = ocm.gardener.find_upgrade_vector(
        component_id=cid,
        version_lookup=lambda _: ['1.0.0', '2.0.0'],
    )

    assert vector is None


def test_find_upgrade_vector_ignores_prerelease():
    cid = ocm.ComponentIdentity(
        name='example.com/comp',
        version='1.0.0',
    )

    vector = ocm.gardener.find_upgrade_vector(
        component_id=cid,
        version_lookup=lambda _: ['1.0.0', '1.1.0-dev'],
        ignore_prerelease_versions=True,
    )

    assert vector is None
