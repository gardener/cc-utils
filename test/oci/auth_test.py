import functools

import oci.auth as oa

fake_cfg = object()

mk_cfg = functools.partial(
    oa.OciConfig,
    credentials=fake_cfg,
)

ro = oa.Privileges.READONLY
rw = oa.Privileges.READWRITE
rd = None


def test_OciConfig_matching():
    no_restrictions = mk_cfg(privileges=rd, url_prefixes=())

    # if no restrictions are specified, everything should match
    assert no_restrictions.valid_for(image_reference='foo', privileges=rd)
    assert no_restrictions.valid_for(image_reference='foo', privileges=ro)
    assert no_restrictions.valid_for(image_reference='foo', privileges=rw)

    with_url_prefix = mk_cfg(privileges=rd, url_prefixes=('example1.org/foo', 'example2.org/bar'))

    assert with_url_prefix.valid_for(image_reference='example1.org/foo')
    assert with_url_prefix.valid_for(image_reference='example1.org/foo/bar')
    assert with_url_prefix.valid_for(image_reference='example1.org/foo/bar', privileges=ro)
    assert with_url_prefix.valid_for(image_reference='example1.org/foo/bar', privileges=rw)
    assert with_url_prefix.valid_for(image_reference='example2.org/bar/foo')
    assert not with_url_prefix.valid_for(image_reference='not.example.org/foo')

    # will be noramlised to `registry-1.docker.io/library/alpine`
    w_normalised_prefix = mk_cfg(privileges=rd, url_prefixes=('alpine',))

    assert w_normalised_prefix.valid_for(image_reference='alpine:3')
    assert w_normalised_prefix.valid_for(image_reference='registry-1.docker.io/library/alpine/foo')
