import oci.util as ou


def test_normalise_image_reference():
    # do not change fully qualified reference
    reference = 'foo.io/my/image:1.2.3'
    assert ou.normalise_image_reference(reference) == reference

    # prepend default registry (docker.io) if no host given
    reference = 'my/image:1.2.3'
    assert ou.normalise_image_reference(reference)  == 'registry-1.docker.io/' + reference

    # insert 'library' if no "owner" is given
    reference = 'alpine:1.2.3'
    assert ou.normalise_image_reference(reference) == 'registry-1.docker.io/library/' + reference

    # localhost with port is a valid registry host, must not be rewritten
    reference = 'localhost:8443/my/image:1.2.3'
    assert ou.normalise_image_reference(reference) == reference


def test_image_reference_matches_prefix():
    m = ou.image_reference_matches_prefix
    # host + path, descendant matches
    assert m('example1.org/foo', 'example1.org/foo')
    assert m('example1.org/foo/bar', 'example1.org/foo')
    # host-only prefix matches any repo on that host
    assert m('eu.gcr.io/x:1', 'eu.gcr.io')
    # docker-hub normalisation preserved
    assert m('alpine:3', 'alpine')
    assert m('registry-1.docker.io/library/alpine/foo', 'alpine')
    # lookalike host must NOT match
    assert not m('example1.org.attacker.example/foo', 'example1.org/foo')
    assert not m('eu.gcr.io.attacker.example/x:1', 'eu.gcr.io')
    # sibling path must NOT match (segment boundary)
    assert not m('example.org/bar-evil', 'example.org/bar')
    # port must match exactly
    assert m('reg.example:5000/x', 'reg.example:5000')
    assert not m('reg.example:5000/x', 'reg.example:6000')
    # dot-less host-only prefix with port still matches despite normalisation
    assert m('myregistry:5000/x', 'myregistry:5000')
    assert not m('myregistry:5000/x', 'otherreg:5000')
    # netloc case-insensitive, path case-sensitive
    assert m('EU.GCR.IO/foo', 'eu.gcr.io/foo')
    assert not m('eu.gcr.io/Foo', 'eu.gcr.io/foo')
    # scheme-bearing prefix does not raise
    assert isinstance(m('index.docker.io/x', 'https://index.docker.io/v1/'), bool)
    # relative/local ref never matches a host-scoped prefix
    assert not m('/local/x', 'eu.gcr.io')
    # digest-only reference on the host (no repository path) still matches a host-only prefix
    assert m('eu.gcr.io@sha256:' + 'a' * 64, 'eu.gcr.io')
    assert not m('eu.gcr.io.attacker.example@sha256:' + 'a' * 64, 'eu.gcr.io')
