import model.bdba as bdba


def _mk_bdba(**raw):
    raw.setdefault('api_url', 'https://bdba.example.org/api')
    return bdba.BDBAConfig(name='test', raw_dict=raw)


def test_matches_requires_equal_host():
    cfg = _mk_bdba(api_url='https://bdba.example.org/api')
    assert cfg.matches(base_url='https://bdba.example.org/api') >= 1
    # lookalike host must NOT match (was startswith-based)
    assert cfg.matches(base_url='https://bdba.example.org.attacker.example/api') == -1
