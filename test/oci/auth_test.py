import functools
import base64
import json

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

    # lookalike host and sibling path must NOT match
    assert not with_url_prefix.valid_for(image_reference='example1.org.attacker.example/foo')
    assert not with_url_prefix.valid_for(image_reference='example2.org/bar-evil')


def test_docker_credentials_lookup_respects_port(tmp_path):
    # the docker-cfg lookup must match the full netloc (host+port), not host only
    auth = base64.b64encode(b'user:secret').decode('utf-8')
    cfg = tmp_path / 'config.json'
    cfg.write_text(json.dumps({'auths': {'reg.example:5000': {'auth': auth}}}))

    lookup = oa.docker_credentials_lookup(docker_cfg=str(cfg))

    creds = lookup('reg.example:5000/x', absent_ok=True)
    assert creds is not None
    assert creds.username == 'user'

    # a different port must NOT pick up the :5000 entry
    assert lookup('reg.example:6000/x', absent_ok=True) is None


def test_docker_credentials_lookup_portless_entry_is_wildcard(tmp_path):
    # a port-less host entry still matches any port on that host (unchanged behaviour)
    auth = base64.b64encode(b'user:secret').decode('utf-8')
    cfg = tmp_path / 'config.json'
    cfg.write_text(json.dumps({'auths': {'reg.example': {'auth': auth}}}))

    lookup = oa.docker_credentials_lookup(docker_cfg=str(cfg))

    assert lookup('reg.example/x', absent_ok=True).username == 'user'
    assert lookup('reg.example:5000/x', absent_ok=True).username == 'user'


def test_docker_credentials_lookup_credhelpers_respects_port(tmp_path):
    # the credHelpers plane must not select a `host:5000` helper entry for `host:6000`
    # (the static `auths` plane already enforces this).
    # With FAIL policy and a non-existent helper binary, selecting the :5000 helper would try to
    # invoke it and raise RuntimeError; correct behaviour selects no helper and returns None.
    cfg = tmp_path / 'config.json'
    cfg.write_text(json.dumps({'credHelpers': {'reg.example:5000': 'does-not-exist'}}))

    lookup = oa.docker_credentials_lookup(
        docker_cfg=str(cfg),
        credential_helper_policy=oa.CredentialHelperPolicy.FAIL,
    )

    # exact port still selects (and raises, proving the helper WAS chosen for the right port)
    try:
        lookup('reg.example:5000/x', absent_ok=True)
        raise AssertionError('expected the :5000 helper to be selected and invoked')
    except RuntimeError:
        pass

    # a different port must NOT select the :5000 helper → no invocation, no RuntimeError
    assert lookup('reg.example:6000/x', absent_ok=True) is None
