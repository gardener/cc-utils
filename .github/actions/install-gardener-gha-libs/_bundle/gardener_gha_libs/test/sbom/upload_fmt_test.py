#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
'''Unit tests for upload.py _fmt_id / _filename for CBOM resources.'''
import importlib.util
import os

import ocm
import sbom.iter as si
import sbom.oci as soci


def _make_sbom_resource(extra_identity: dict) -> ocm.Resource:
    return ocm.Resource(
        name='some-image',
        version='v1.0.0',
        type=soci.CYCLONEDX_JSON_MEDIA_TYPE,
        relation=ocm.ResourceRelation.EXTERNAL,
        extraIdentity=extra_identity,
        access=ocm.OciAccess(imageReference='registry.example.com/repo@sha256:ff'),
    )


def _make_mapping(resource: ocm.Resource) -> si.SbomMapping:
    component = ocm.Component(
        name='github.com/foo/bar',
        version='v1',
        repositoryContexts=[],
        provider='test',
        sources=[],
        componentReferences=[],
        resources=[],
    )
    return si.SbomMapping(
        source=si.SbomSource.OCM,
        component=component,
        resource=resource,
        sbom=resource,
    )


# import the functions under test from the action script
_upload_py = os.path.join(
    os.path.dirname(__file__),
    '../../.github/actions/sbom-upload/upload.py',
)
_spec = importlib.util.spec_from_file_location('upload', _upload_py)
_upload = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_upload)
_fmt_id = _upload._fmt_id
_filename = _upload._filename


def test_fmt_id_sbom():
    r = _make_sbom_resource({'sbom-format': 'spdx-2.3', 'version': 'v1.0.0'})
    m = _make_mapping(r)
    assert _fmt_id(m) == 'spdx-2.3'


def test_fmt_id_cyclonedx():
    r = _make_sbom_resource({'sbom-format': 'cyclonedx-1.6', 'version': 'v1.0.0'})
    m = _make_mapping(r)
    assert _fmt_id(m) == 'cyclonedx-1.6'


def test_fmt_id_cbom():
    r = _make_sbom_resource({'cbom-format': 'cyclonedx-1.6', 'version': 'v1.0.0'})
    m = _make_mapping(r)
    assert _fmt_id(m) == 'cbom-cyclonedx-1.6'


def test_fmt_id_neither_returns_none():
    r = _make_sbom_resource({'version': 'v1.0.0'})
    m = _make_mapping(r)
    assert _fmt_id(m) is None


def test_filename_excludes_cbom_format_key():
    component = ocm.Component(
        name='github.com/foo/bar',
        version='v1',
        repositoryContexts=[],
        provider='test',
        sources=[],
        componentReferences=[],
        resources=[],
    )
    resource = _make_sbom_resource({'cbom-format': 'cyclonedx-1.6', 'version': 'v1.0.0'})
    name = _filename(component, resource, 'cbom-cyclonedx-1.6')
    # should not contain 'cyclonedx-1.6' twice (the format value should not appear in name)
    assert name.count('cyclonedx-1.6') == 1
    assert name.endswith('.sbom.cbom-cyclonedx-1.6')


def test_filename_excludes_sbom_format_key():
    component = ocm.Component(
        name='github.com/foo/bar',
        version='v1',
        repositoryContexts=[],
        provider='test',
        sources=[],
        componentReferences=[],
        resources=[],
    )
    resource = _make_sbom_resource({'sbom-format': 'spdx-2.3', 'version': 'v1.0.0'})
    name = _filename(component, resource, 'spdx-2.3')
    assert name.endswith('.sbom.spdx-2.3')
    assert name.count('spdx-2.3') == 1
