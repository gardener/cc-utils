# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import ensure
import functools

import ci.log
import ci.util
import concourse.client.api
import ctx
import model.concourse


def lookup_cc_team_cfg(
    concourse_cfg,
    cfg_set,
    team_name,
) -> model.concourse.ConcourseTeamConfig:
    for cc_team_cfg in cfg_set._cfg_elements('concourse_team_cfg'):
        if cc_team_cfg.team_name() != team_name:
            continue
        if concourse_cfg.concourse_endpoint_name() != cc_team_cfg.concourse_endpoint_name():
            continue

        return cc_team_cfg
    raise KeyError(f'No concourse team config for team name {team_name} found')


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
    concourse_cfg_name: str,
    team_name: str,
    cfg_factory=None,
):
    '''Create a Concourse API client for the team with the given name on the given concourse instance

    As team names are not necessarily unique across all our Concourse instances, this function
    will perform a lookup using the given config-factory/-set. If no config-factory or -set is given,
    all config that is available by default will be considered.
    An error will be raised if no team with the requested name can be found for the given Concourse
    instance in the config.
    '''
    if not cfg_factory:
        cfg_factory = ci.util.ctx().cfg_factory()

    concourse_cfg = cfg_factory.concourse(concourse_cfg_name)

    concourse_team_config = lookup_cc_team_cfg(
        concourse_cfg=concourse_cfg,
        cfg_set=cfg_factory,
        team_name=team_name,
    )
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

    concourse_team_config = lookup_cc_team_cfg(
        concourse_cfg=cfg_set.concourse(),
        cfg_set=cfg_set,
        team_name=team_name,
    )
    concourse_endpoint = cfg_set.concourse_endpoint(
        concourse_team_config.concourse_endpoint_name()
    )
    return client_from_parameters(
        base_url=concourse_endpoint.base_url(),
        password=concourse_team_config.password(),
        team_name=team_name,
        username=concourse_team_config.username(),
    )
