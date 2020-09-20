# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import warnings

from ensure import ensure_annotations
from urllib3.exceptions import InsecureRequestWarning

import functools

import ci.util

from .api import ConcourseApiFactory
from model.concourse import ConcourseConfig


warnings.filterwarnings('ignore', 'Unverified HTTPS request is being made.*', InsecureRequestWarning)

'''
An implementation of the (undocumented [0]) RESTful HTTP API offered by concourse
[1]. It was reverse-engineered based on [2], as well using Chrome developer tools and
POST-Man [3].

Usage:
------

Users will probably want to create an instance of ConcourseApiVX, passing a
ConcourseConfig object to the `from_cfg` factory function.

Other types defined in this module are not intended to be instantiated by users.

[0] https://github.com/concourse/concourse/issues/1122
[1] https://concourse.ci
[2] https://github.com/concourse/concourse/blob/master/atc/routes.go
[3] https://www.getpostman.com/
'''


@functools.lru_cache()
@ensure_annotations
def from_cfg(concourse_cfg: ConcourseConfig, team_name: str, verify_ssl=False):
    '''
    Helper method to get Concourse API object
    '''
    base_url = concourse_cfg.ingress_url()
    cfg_factory = ci.util.ctx().cfg_factory()
    concourse_uam_cfg_name = concourse_cfg.concourse_uam_config()
    concourse_uam_cfg = cfg_factory.concourse_uam(concourse_uam_cfg_name)
    concourse_team = concourse_uam_cfg.team(team_name)
    team_name = concourse_team.teamname()
    username = concourse_team.username()
    password = concourse_team.password()
    concourse_api_version = concourse_cfg.compatible_api_version()

    concourse_api = ConcourseApiFactory.create_api(
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
