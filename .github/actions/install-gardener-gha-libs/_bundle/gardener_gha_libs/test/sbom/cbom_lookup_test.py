#!/usr/bin/env python3
# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0
'''Unit tests for sbom.cbom.lookup_cbom_referrer (CBOM referrer cache lookup).'''
import os
import sys
import unittest.mock as mock

# ensure the project root wins over any stale pip-installed sbom/ package
_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _root not in sys.path:
    sys.path.insert(0, _root)

import sbom.cbom as cbom


def _referrer(digest, artifact_type=cbom.CBOM_ARTIFACT_TYPE):
    r = mock.Mock()
    r.digest = digest
    r.artifact_type = artifact_type
    return r


def test_cache_hit_returns_first_referrer_digest():
    oci_client = mock.Mock()
    oci_client.referrers.return_value = (
        _referrer('sha256:aaa'),
        _referrer('sha256:bbb'),
    )

    digest = cbom.lookup_cbom_referrer(
        image_ref='example.org/img@sha256:deadbeef',
        oci_client=oci_client,
    )

    assert digest == 'sha256:aaa'
    # must filter by the CBOM artifact type so plain-SBOM CycloneDX referrers are excluded
    _, kwargs = oci_client.referrers.call_args
    assert kwargs['artifact_type'] == cbom.CBOM_ARTIFACT_TYPE
    assert kwargs['absent_ok'] is True


def test_no_referrers_returns_none():
    oci_client = mock.Mock()
    oci_client.referrers.return_value = ()  # API supported, no entries

    assert cbom.lookup_cbom_referrer(
        image_ref='example.org/img@sha256:deadbeef',
        oci_client=oci_client,
    ) is None


def test_referrers_api_unsupported_returns_none():
    oci_client = mock.Mock()
    oci_client.referrers.return_value = None  # referrers API not supported

    assert cbom.lookup_cbom_referrer(
        image_ref='example.org/img@sha256:deadbeef',
        oci_client=oci_client,
    ) is None
