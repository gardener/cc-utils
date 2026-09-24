# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

import hashlib
import json

import pytest

import ctt.oci_util


DOCKER_MANIFEST_V2 = 'application/vnd.docker.distribution.manifest.v2+json'
DOCKER_MANIFEST_LIST = 'application/vnd.docker.distribution.manifest.list.v2+json'


def test_parse_attribute_path():
    parse = ctt.oci_util.parse_attribute_path

    assert parse('features') == (('features', False),)
    assert parse('manifests[].platform.features') == (
        ('manifests', True),
        ('platform', False),
        ('features', False),
    )

    for invalid in ('', 'a..b', 'manifests[]', 'a[].b[]', '[]'):
        with pytest.raises(ValueError):
            parse(invalid)


def test_strip_attribute():
    parse = ctt.oci_util.parse_attribute_path

    document = {
        'manifests': [
            {'platform': {'os': 'linux', 'features': ['sse4']}},
            {'platform': {'os': 'linux'}},
            'not-a-dict',
        ],
    }
    assert ctt.oci_util.strip_attribute(document, parse('manifests[].platform.features'))
    assert document == {
        'manifests': [
            {'platform': {'os': 'linux'}},
            {'platform': {'os': 'linux'}},
            'not-a-dict',
        ],
    }

    # tolerant of absent attributes / type-mismatches
    assert not ctt.oci_util.strip_attribute(document, parse('manifests[].platform.features'))
    assert not ctt.oci_util.strip_attribute(document, parse('does.not.exist'))
    assert not ctt.oci_util.strip_attribute({'a': 42}, parse('a.b'))
    assert not ctt.oci_util.strip_attribute({'a': 'str'}, parse('a[].b'))

    # top-level attribute
    document = {'features': ['x'], 'keep': 1}
    assert ctt.oci_util.strip_attribute(document, parse('features'))
    assert document == {'keep': 1}


class FakeOciClient:
    def __init__(self, manifests: dict, blobs: dict=None):
        self._manifests = dict(manifests) # ref -> (raw-bytes, content-type)
        self._blobs = dict(blobs or {})   # digest -> bytes
        self.pushed_manifests = {}        # ref -> manifest (as passed)
        self.pushed_blobs = {}            # digest -> bytes

    def manifest_raw(self, image_reference, accept=None):
        raw, content_type = self._manifests[str(image_reference)]

        class Response:
            text = raw.decode('utf-8')
            headers = {'Content-Type': content_type}
        return Response()

    def head_blob(self, image_reference, digest, absent_ok=True):
        # pretend blobs are always absent in the target repository
        class Response:
            ok = False

            def __bool__(self):
                return self.ok
        return Response()

    def blob(self, image_reference, digest, absent_ok=False, **kwargs):
        content = self._blobs[digest]

        class Response:
            pass
        response = Response()
        response.content = content
        response.iter_content = lambda chunk_size: iter([content])
        return response

    def put_blob(self, image_reference, digest, octets_count, data):
        self.pushed_blobs[digest] = data

    def put_manifest(self, image_reference, manifest):
        self.pushed_manifests[str(image_reference)] = manifest
        return manifest


def _mk_single_manifest(extra: dict=None):
    manifest = {
        'schemaVersion': 2,
        'mediaType': DOCKER_MANIFEST_V2,
        'config': {
            'mediaType': 'application/vnd.docker.container.image.v1+json',
            'digest': 'sha256:cfg',
            'size': 2,
        },
        'layers': [{
            'mediaType': 'application/vnd.docker.image.rootfs.diff.tar.gzip',
            'digest': 'sha256:layer',
            'size': 5,
        }],
    }
    manifest.update(extra or {})
    return json.dumps(manifest).encode('utf-8')


def test_strip_attributes_manifest_list():
    child_raw = _mk_single_manifest()
    child_digest = f'sha256:{hashlib.sha256(child_raw).hexdigest()}'

    index = json.dumps({
        'schemaVersion': 2,
        'mediaType': DOCKER_MANIFEST_LIST,
        'manifests': [{
            'mediaType': DOCKER_MANIFEST_V2,
            'digest': child_digest,
            'size': len(child_raw),
            'platform': {
                'architecture': 'amd64',
                'os': 'linux',
                'features': ['sse4'],
            },
        }],
    }).encode('utf-8')

    src_ref = 'src.example.com/my/image:1.0'
    child_src_ref = f'src.example.com/my/image@{child_digest}'

    client = FakeOciClient(
        manifests={
            src_ref: (index, DOCKER_MANIFEST_LIST),
            child_src_ref: (child_raw, DOCKER_MANIFEST_V2),
        },
        blobs={'sha256:cfg': b'{}', 'sha256:layer': b'layer'},
    )

    res, tgt_ref, manifest_raw = ctt.oci_util.replicate_with_stripped_manifest_attributes(
        source_ref=src_ref,
        target_ref=f'tgt.example.com/my/image:1.0@sha256:{"0" * 64}',
        strip_manifest_attributes=['manifests[].platform.features'],
        oci_client=client,
    )

    # child manifest replicated verbatim (digest preserved in index entry)
    assert f'tgt.example.com/my/image@{child_digest}' in client.pushed_manifests

    pushed_index = json.loads(client.pushed_manifests[tgt_ref])
    assert 'features' not in json.dumps(pushed_index)
    assert pushed_index['manifests'] == [{
        'mediaType': DOCKER_MANIFEST_V2,
        'digest': child_digest,
        'size': len(child_raw),
        'platform': {'architecture': 'amd64', 'os': 'linux'},
    }]

    # returned ref/bytes correspond to pushed (stripped) manifest; symbolical tag retained
    assert tgt_ref == (
        'tgt.example.com/my/image:1.0@sha256:'
        f'{hashlib.sha256(manifest_raw).hexdigest()}'
    )


def test_strip_attributes_single_manifest():
    single_raw = _mk_single_manifest(extra={'features': ['sse4']})

    src_ref = 'src.example.com/my/image:1.0'
    client = FakeOciClient(
        manifests={src_ref: (single_raw, DOCKER_MANIFEST_V2)},
        blobs={'sha256:cfg': b'{}', 'sha256:layer': b'layer'},
    )

    res, tgt_ref, manifest_raw = ctt.oci_util.replicate_with_stripped_manifest_attributes(
        source_ref=src_ref,
        target_ref=f'tgt.example.com/my/image:1.0@sha256:{"0" * 64}',
        strip_manifest_attributes=['features'],
        oci_client=client,
    )

    # blobs copied before manifest-push
    assert sorted(client.pushed_blobs) == ['sha256:cfg', 'sha256:layer']

    pushed = json.loads(client.pushed_manifests[tgt_ref])
    assert 'features' not in pushed
    assert pushed['config']['digest'] == 'sha256:cfg'
    assert tgt_ref == (
        'tgt.example.com/my/image:1.0@sha256:'
        f'{hashlib.sha256(manifest_raw).hexdigest()}'
    )
