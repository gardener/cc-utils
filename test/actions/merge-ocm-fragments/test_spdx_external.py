# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

import os
import sys
import tempfile
import unittest.mock

sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(
            os.path.dirname(__file__), '..', '..', '..', '.github', 'actions',
            'merge-ocm-fragments',
        )
    ),
)

import yaml

import spdx_external


def _stale_sbom(name='img', version='v1', fmt='spdx-2.3'):
    return {
        'name': name,
        'version': version,
        'type': 'application/spdx+json',
        'relation': 'external',
        'access': {'type': 'localBlob/v1', 'localReference': 'sha256:old'},
        'extraIdentity': {'sbom-format': fmt, 'version': version},
    }


def _stale_cbom(name='img', version='v1'):
    return {
        'name': name,
        'version': version,
        'type': 'application/vnd.cyclonedx+json',
        'relation': 'external',
        'access': {'type': 'localBlob/v1', 'localReference': 'sha256:old-cbom'},
        'extraIdentity': {'cbom-format': 'cyclonedx-1.6', 'version': version},
    }


def _external_oci(name='img', version='v1', image_ref='registry.example.com/img:v1'):
    return {
        'name': name,
        'version': version,
        'type': 'ociImage',
        'relation': 'external',
        'access': {'type': 'ociRegistry', 'imageReference': image_ref},
        'extraIdentity': {},
    }


def _run_process(tmp_dir, resources):
    '''
    Call process_external_resources with all heavy I/O mocked out.
    Returns the resources list from the written component descriptor.
    '''
    cd_path = os.path.join(tmp_dir, 'component-descriptor.yaml')
    cd = {
        'component': {
            'name': 'example.com/comp',
            'version': '1.0.0',
            'resources': list(resources),
        }
    }
    with open(cd_path, 'w') as f:
        yaml.safe_dump(cd, f)

    fresh_spdx = {
        'name': 'img', 'version': 'v1', 'type': 'application/spdx+json',
        'relation': 'external',
        'access': {'type': 'localBlob/v1', 'localReference': 'sha256:fresh-spdx'},
        'extraIdentity': {'sbom-format': 'spdx-2.3', 'version': 'v1'},
    }
    fresh_cdx = {
        'name': 'img', 'version': 'v1', 'type': 'application/vnd.cyclonedx+json',
        'relation': 'external',
        'access': {'type': 'localBlob/v1', 'localReference': 'sha256:fresh-cdx'},
        'extraIdentity': {'sbom-format': 'cyclonedx-1.6', 'version': 'v1'},
    }

    fake_cache_result = spdx_external._CacheResult(
        info=spdx_external._ImageInfo(
            resource=_external_oci(),
            digest_ref='registry.example.com/img@sha256:abc123',
            source_digest='sha256:abc123',
            compressed_layer_bytes=1024,
        ),
        cached={
            'spdx-2.3': b'{"spdxVersion":"SPDX-2.3"}',
            'cyclonedx-1.6': b'{"bomFormat":"CycloneDX"}',
            'cbom-cyclonedx-1.6': b'{"bomFormat":"CycloneDX","metadata":{"component":{}}}',
        },
    )

    with (
        unittest.mock.patch.object(
            spdx_external, '_resolve_image_info',
            return_value=fake_cache_result.info,
        ),
        unittest.mock.patch.object(
            spdx_external, '_check_cache',
            return_value=fake_cache_result,
        ),
        unittest.mock.patch.object(
            spdx_external, '_build_sbom_resources',
            return_value=(fresh_spdx, fresh_cdx),
        ),
        unittest.mock.patch.object(
            spdx_external, '_store_blob',
            side_effect=lambda d, data: f'sha256:{hash(data):x}',
        ),
        unittest.mock.patch.object(
            spdx_external, '_syft_version_from_spdx',
            return_value='1.0.0',
        ),
        unittest.mock.patch.object(
            spdx_external, '_write_step_summary',
        ),
        unittest.mock.patch(
            'oci.auth.docker_credentials_lookup',
            return_value=lambda *a, **kw: None,
        ),
        unittest.mock.patch('oci.client.Client'),
    ):
        spdx_external.process_external_resources(
            component_descriptor_path=cd_path,
            out_dir=tmp_dir,
            cache_registry='registry.example.com',
        )

    with open(cd_path) as f:
        result = yaml.safe_load(f)
    return result['component']['resources']


def test_stale_sboms_replaced_on_rerun():
    '''Stale SBOM/CBOM entries are stripped before injecting fresh ones.'''
    initial = [
        _external_oci(),
        _stale_sbom(fmt='spdx-2.3'),
        _stale_sbom(fmt='cyclonedx-1.6'),
        _stale_cbom(),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        resources = _run_process(tmp, initial)

    sbom_resources = [
        r for r in resources
        if 'sbom-format' in (r.get('extraIdentity') or {})
        or 'cbom-format' in (r.get('extraIdentity') or {})
    ]
    # fresh pair replaces all three stale entries — no duplicates
    assert len(sbom_resources) == 2
    refs = {r['access']['localReference'] for r in sbom_resources}
    assert 'sha256:old' not in refs
    assert 'sha256:old-cbom' not in refs


def test_no_existing_sboms_works():
    '''First run (no pre-existing SBOMs) succeeds without errors.'''
    initial = [_external_oci()]
    with tempfile.TemporaryDirectory() as tmp:
        resources = _run_process(tmp, initial)

    sbom_resources = [
        r for r in resources
        if 'sbom-format' in (r.get('extraIdentity') or {})
    ]
    assert len(sbom_resources) == 2
