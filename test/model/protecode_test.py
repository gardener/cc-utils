# Copyright (c) 2023-2024 SAP SE or an SAP affiliate company. All rights reserved. This file is
# licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
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
