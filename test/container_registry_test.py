import model.container_registry as cr


def _mk_cfg(**raw):
    raw.setdefault('username', 'u')
    raw.setdefault('password', 'p')
    raw.setdefault('privileges', 'readonly')
    return cr.ContainerRegistryConfig(name='test', raw_dict=raw)


def test_image_ref_matches_boundary():
    cfg = _mk_cfg(image_reference_prefixes=['eu.gcr.io/team'])
    assert cfg.image_ref_matches('eu.gcr.io/team/img:1')
    # lookalike host must NOT match
    assert not cfg.image_ref_matches('eu.gcr.io.attacker.example/team/img:1')
    # sibling path must NOT match (segment boundary)
    assert not cfg.image_ref_matches('eu.gcr.io/team-evil/img:1')


def test_as_docker_auths_does_not_widen_path_scoped_prefix():
    cfg = _mk_cfg(image_reference_prefixes=['eu.gcr.io/team'])
    auths = cfg.as_docker_auths()
    # a path-scoped prefix must not be widened to a whole-host docker auth
    assert 'eu.gcr.io' not in auths

    cfg2 = _mk_cfg(image_reference_prefixes=['eu.gcr.io'])
    # a host-only prefix is still emitted
    assert 'eu.gcr.io' in cfg2.as_docker_auths()


def test_image_ref_matches_skips_malformed_prefix():
    # a malformed prefix must not raise (which would abort find_config for ALL registries);
    # it is skipped, and a valid sibling prefix still matches
    cfg = _mk_cfg(image_reference_prefixes=['', 'eu.gcr.io/team'])
    assert cfg.image_ref_matches('eu.gcr.io/team/img:1')
    assert not cfg.image_ref_matches('other.example/x')
