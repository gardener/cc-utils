# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import model.protecode


def test_group_id_mismatch():
    cfg = model.protecode.ProtecodeConfig(
        name='foo',
        raw_dict={
            'group_ids': [1,2]
        },
        type_name='protecode',
    )
    assert cfg.matches(group_id=3) == -1


def test_base_url_mismatch():
    cfg = model.protecode.ProtecodeConfig(
        name='foo',
        raw_dict={
            'api_url': 'http://foo.bar'
        },
        type_name='protecode',
    )
    assert cfg.matches(base_url='http://bar.foo') == -1


def test_base_url_and_group_id_match():
    cfg = model.protecode.ProtecodeConfig(
        name='foo',
        raw_dict={
            'api_url': 'http://foo.bar:333/xxx?y=4',
            'group_ids': [1,2],
        },
        type_name='protecode',
    )
    assert cfg.matches(
        base_url='http://foo.bar',
        group_id=2,
    ) == 2


def test_cfg_lookup_most_specific():
    loser_1 = model.protecode.ProtecodeConfig(
        name='loser',
        raw_dict={
            'api_url': 'http://foo.bar:333/xxx?y=4',
        },
        type_name='protecode',
    )
    loser_2 = model.protecode.ProtecodeConfig(
        name='loser',
        raw_dict={
            'api_url': 'http://bar.bar',
        },
        type_name='protecode',
    )
    winner = model.protecode.ProtecodeConfig(
        name='winner',
        raw_dict={
            'api_url': 'http://foo.bar:333/xxx?y=4',
            'group_ids': [1,2],
        },
        type_name='protecode',
    )
    assert model.protecode.find_config(
        base_url='http://foo.bar',
        group_id=2,
        config_candidates=[loser_1, loser_2, winner],
    ).name() == 'winner'


def test_cfg_lookup_none():
    cfg = model.protecode.ProtecodeConfig(
        name='foo',
        raw_dict={
            'api_url': 'http://foo.bar:333/xxx?y=4',
            'group_ids': [1,2],
        },
        type_name='protecode',
    )
    assert model.protecode.find_config(
        base_url='http://foo.bar',
        group_id=3,
        config_candidates=[cfg,],
    ) == None
