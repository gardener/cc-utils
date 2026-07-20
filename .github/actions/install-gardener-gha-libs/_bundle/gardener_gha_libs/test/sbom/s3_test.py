#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
'''Unit tests for sbom.s3 and related helpers.'''
import pytest

import sbom.s3 as ss3


def test_s3_url_with_region():
    assert ss3.s3_url('my-bucket', 'path/to/key', 'eu-west-1') == (
        'https://my-bucket.s3.eu-west-1.amazonaws.com/path/to/key'
    )


def test_s3_url_without_region():
    assert ss3.s3_url('my-bucket', 'some/key') == (
        'https://my-bucket.s3.amazonaws.com/some/key'
    )


def test_iter_s3_object(monkeypatch):
    payload = b'hello world' * 1000
    import unittest.mock as mock

    fake_resp = mock.MagicMock()
    fake_resp.__enter__ = lambda s: s
    fake_resp.__exit__ = mock.MagicMock(return_value=False)
    fake_resp.read = mock.MagicMock(side_effect=[
        payload[:4096], payload[4096:], b'',
    ])

    with mock.patch('urllib.request.urlopen', return_value=fake_resp):
        chunks = list(ss3.iter_s3_object('bucket', 'key'))
    assert b''.join(chunks) == payload[:4096] + payload[4096:]


@pytest.mark.parametrize('s, expected', [
    ('my-bucket',      'my-bucket'),
    ('MY_BUCKET',      'my_bucket'),
    ('a/b/c',          'a_b_c'),
    ('foo--bar',       'foo--bar'),
    ('foo__bar',       'foo_bar'),       # consecutive underscores collapsed
    ('foo/BAR/baz',    'foo_bar_baz'),
    ('hello.world',    'hello.world'),
    ('.leading',       '.leading'),      # dots are valid OCI path chars; kept as-is
    ('trailing.',      'trailing.'),
])
def test_mangle_s3_path(s, expected):
    assert ss3.mangle_s3_path(s) == expected


def test_synthetic_oci_ref_structure():
    ref = ss3.synthetic_oci_ref(
        registry_base='europe-docker.pkg.dev/gardener-project/snapshots',
        bucket='gardenlinux-github-releases',
        key='releases/1337.0/amd64-gcp.tar.gz',
        content_digest='sha256:abcdef1234',
    )
    assert ref.startswith('europe-docker.pkg.dev/gardener-project/snapshots/sbom-s3/')
    assert 'gardenlinux-github-releases' in ref
    assert '@sha256:abcdef1234' in ref


def test_synthetic_oci_ref_no_double_slash():
    ref = ss3.synthetic_oci_ref(
        registry_base='europe-docker.pkg.dev/foo/',   # trailing slash
        bucket='b',
        key='k',
        content_digest='sha256:ff',
    )
    assert '//' not in ref


def test_synthetic_oci_ref_repo_split():
    ref = ss3.synthetic_oci_ref(
        registry_base='registry.example.com/base',
        bucket='bucket',
        key='some/object/key.tar.gz',
        content_digest='sha256:0011',
    )
    repo_ref, digest = ref.split('@')
    assert digest == 'sha256:0011'
    assert repo_ref.startswith('registry.example.com/base/sbom-s3/')
    # no '@' in the repo part
    assert '@' not in repo_ref
