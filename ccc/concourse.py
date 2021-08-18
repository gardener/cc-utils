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
import functools

import ci.util
import concourse.client
import ctx
import model.concourse

import ensure
import concourse.client.api


def lookup_cc_uam(cfg_set, team_name) -> model.concourse.ConcourseUAM:
    for cc_uam in cfg_set._cfg_elements('concourse_uam'):
        if cc_uam.team_name() == team_name:
            return cc_uam

    raise RuntimeError(f'No concourse uam for team name {team_name} found')


@ensure.ensure_annotations
def from_parameters(
    base_url: str,
    password: str,
    team_name: str,
    username: str,
    verify_ssl: bool = True,
    concourse_api_version=None,
) -> concourse.client.api.ConcourseApiBase:
    """
    initialises the concourse api and automatically logs in user
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


@functools.cache
def client_from_cfg(
    cfg_set,
    team_name: str = '',
) -> concourse.client.api.ConcourseApiBase:

    if not team_name:
        team_name = ci.util.check_env('CONCOURSE_CURRENT_TEAM')

    cc_uam = lookup_cc_uam(cfg_set=cfg_set, team_name=team_name)
    cc_endpoint = cfg_set.concourse_endpoint(cc_uam.concourse_endpoint_name())

    return from_parameters(
        base_url=cc_endpoint.base_url(),
        password=cc_uam.password(),
        team_name=team_name,
        username=cc_uam.username(),
    )


@functools.cache
def client_from_env(
    team_name: str=None,
    cfg_set=None,
) -> concourse.client.api.ConcourseApiBase:
    """
    returns a concourse-client w/ the credentials valid for the current execution environment.
    Note that this function must only be called if running in a "central" cicd-job.
    The returned client is authorised to perform operations in the same concourse-team as the
    job calling this function.

    if the (optional) team_name is specified, the returned client is not guaranteed to have the
    required authorisation.
    """

    if not team_name:
        team_name = ci.util.check_env('CONCOURSE_CURRENT_TEAM')

    if not cfg_set:
        cfg_set = ctx.cfg_set()

    cc_uam = lookup_cc_uam(cfg_set=cfg_set, team_name=team_name)
    cc_endpoint = cfg_set.concourse_endpoint(cc_uam.concourse_endpoint_name())

    return from_parameters(
        base_url=cc_endpoint.url(),
        password=cc_uam.password(),
        team_name=team_name,
        username=cc_uam.username(),
    )
