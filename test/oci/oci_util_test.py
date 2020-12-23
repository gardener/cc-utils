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
