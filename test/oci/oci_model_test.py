import hashlib
import pytest

import oci.model as om

example_digest = hashlib.sha256('cafebabe'.encode('utf-8')).hexdigest()


def test_netloc():
    # simple case w/ symbolic tag
    ref = om.OciImageReference('example.org/path:tag')
    assert ref.netloc == 'example.org'

    ref = om.OciImageReference(f'example.org/path@sha256:{example_digest}')
    assert ref.netloc == 'example.org'

    ref = om.OciImageReference('example.org:1234/path:tag')
    assert ref.netloc == 'example.org:1234'

    ref = om.OciImageReference('example.org:1234/path@sha256:{example_digest}')
    assert ref.netloc == 'example.org:1234'

    # special handling to mimic docker-cli
    ref = om.OciImageReference('alpine:3')
    assert ref.netloc == 'registry-1.docker.io'


def test_name():
    ref = om.OciImageReference('example.org/path:tag')
    assert ref.name == 'example.org/path'

    ref = om.OciImageReference(f'example.org/path@sha256:{example_digest}')
    assert ref.name == 'example.org/path'

    ref = om.OciImageReference('example.org:1234/path:tag')
    assert ref.name == 'example.org:1234/path'

    # special handling to mimic docker-cli
    ref = om.OciImageReference('alpine:3')
    assert ref.name == 'registry-1.docker.io/library/alpine'


def test_original_image_reference():
    ref = om.OciImageReference('alpine:3')
    assert ref.original_image_reference == 'alpine:3'


def test_tag():
    ref = om.OciImageReference('alpine:3')
    assert ref.tag == '3'

    ref = om.OciImageReference(f'example.org/path@sha256:{example_digest}')
    assert ref.tag == f'sha256:{example_digest}'

    ref = om.OciImageReference(f'example.org:1234/path@sha256:{example_digest}')
    assert ref.tag == f'sha256:{example_digest}'


def test_tag_type():
    ref = om.OciImageReference('example.org/path:symbolic-tag')
    assert ref.tag_type is om.OciTagType.SYMBOLIC

    ref = om.OciImageReference(f'example.org/path@sha256:{example_digest}')
    assert ref.tag_type is om.OciTagType.DIGEST


def test_str():
    ref = om.OciImageReference('alpine:3')
    assert str(ref) == ref.normalised_image_reference


def test_eq():
    ref1 = om.OciImageReference('alpine:3')
    ref2 = om.OciImageReference('registry-1.docker.io/library/alpine:3')

    assert ref1 == ref2
    assert ref1 == ref1
    assert ref2 == ref2

    ref3 = om.OciImageReference('example.org/path:tag1')

    assert ref1 != ref3


def test_parsed_digest_tag():
    with pytest.raises(ValueError):
        om.OciImageReference('alpine:3').parsed_digest_tag

    ref = om.OciImageReference(f'example.org/path@sha256:{example_digest}')
    alg, dig = ref.parsed_digest_tag

    assert alg == 'sha256'
    assert dig == example_digest.split(':')[-1]

    ref = om.OciImageReference(f'alpine@sha256:{example_digest}')
    alg, dig = ref.parsed_digest_tag

    assert alg == 'sha256'
    assert dig == example_digest.split(':')[-1]
