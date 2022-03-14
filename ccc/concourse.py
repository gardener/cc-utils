# Copyright (c) 2019-2020 SAP SE or an SAP affiliate company. All rights reserved. This file is
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
import ensure
import functools

import ci.log
import ci.util
import concourse.client.api
import ctx


@ensure.ensure_annotations
def client_from_parameters(
    base_url: str,
    password: str,
    team_name: str,
    username: str,
    verify_ssl: bool = True,
    concourse_api_version=None,
) -> concourse.client.api.ConcourseApiBase:
    """
    returns a concourse-client w/ the credentials valid for the current execution environment.
    The returned client is authorised to perform operations in the same concourse-team as the
    credentials provided calling this function.
    """

    concourse_api = concourse.client.api.ConcourseApiFactory.create_api(
        base_url=base_url,
        team_name=team_name,
        verify_ssl=verify_ssl,
        concourse_api_version=concourse_api_version,
    )

    concourse_api.login(
        username=username,
        passwd=password,
    )
    return concourse_api


@functools.lru_cache()
@ensure.ensure_annotations
def client_from_cfg_name(
    concourse_main_team_cfg_name: str,
    team_name: str,
    cfg_factory=None,
):
    if not cfg_factory:
        cfg_factory = ci.util.ctx().cfg_factory()

    concourse_team_config = cfg_factory.concourse_team_cfg(concourse_main_team_cfg_name)
    concourse_endpoint = cfg_factory.concourse_endpoint(
        concourse_team_config.concourse_endpoint_name()
    )
    return client_from_parameters(
        base_url=concourse_endpoint.base_url(),
        password=concourse_team_config.password(),
        team_name=team_name,
        username=concourse_team_config.username(),
    )


def client_from_env(
    team_name: str=None,
    cfg_set=None
):
    if not cfg_set:
        cfg_set = ctx.cfg_set()

    if not team_name:
        team_name = ci.util.check_env('CONCOURSE_CURRENT_TEAM')

    concourse_main_team_cfg_name = cfg_set.concourse().concourse_main_team_config()
    concourse_team_config = cfg_set.concourse_team_cfg(concourse_main_team_cfg_name)
    concourse_endpoint = cfg_set.concourse_endpoint(
        concourse_team_config.concourse_endpoint_name()
    )
    return client_from_parameters(
        base_url=concourse_endpoint.base_url(),
        password=concourse_team_config.password(),
        team_name=team_name,
        username=concourse_team_config.username(),
    )
