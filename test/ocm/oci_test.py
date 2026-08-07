import pytest

import oci.model as om
import ocm.oci


def _entry(digest, annotations=None):
    return om.OciImageManifestListEntry(
        digest=digest,
        mediaType='application/vnd.oci.image.manifest.v1+json',
        size=0,
        annotations=annotations,
    )


def _index(*entries):
    return om.OciImageManifestList(manifests=list(entries))


def test_empty_index_raises():
    with pytest.raises(ValueError, match='empty'):
        ocm.oci.find_component_descriptor_manifest_digest(_index())


def test_single_annotated_manifest_returned():
    entry = _entry('sha256:aaa', {'software.ocm.component.name': 'foo'})
    assert ocm.oci.find_component_descriptor_manifest_digest(_index(entry)) == 'sha256:aaa'


def test_deprecated_componentversion_annotation_accepted():
    entry = _entry('sha256:bbb', {'software.ocm.componentversion': 'v1.0.0'})
    assert ocm.oci.find_component_descriptor_manifest_digest(_index(entry)) == 'sha256:bbb'


def test_component_version_annotation_accepted():
    entry = _entry('sha256:ccc', {'software.ocm.component.version': '1.0.0'})
    assert ocm.oci.find_component_descriptor_manifest_digest(_index(entry)) == 'sha256:ccc'


def test_unannotated_fallback_returns_first():
    first = _entry('sha256:first')
    second = _entry('sha256:second')
    assert ocm.oci.find_component_descriptor_manifest_digest(_index(first, second)) == 'sha256:first'


def test_multiple_annotated_manifests_raises():
    a = _entry('sha256:aaa', {'software.ocm.component.name': 'foo'})
    b = _entry('sha256:bbb', {'software.ocm.component.name': 'bar'})
    with pytest.raises(ValueError, match='multiple'):
        ocm.oci.find_component_descriptor_manifest_digest(_index(a, b))


def test_mixed_annotated_and_plain_returns_annotated():
    plain = _entry('sha256:plain')
    annotated = _entry('sha256:cd', {'software.ocm.component.name': 'foo'})
    assert ocm.oci.find_component_descriptor_manifest_digest(
        _index(plain, annotated)
    ) == 'sha256:cd'
