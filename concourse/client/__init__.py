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

import warnings

from ensure import ensure_annotations
from urllib3.exceptions import InsecureRequestWarning

import functools

import ci.util
from .api import (
    ConcourseApiV5,
)
from .routes import (
    ConcourseApiRoutesV5,
)
from model.concourse import (
    ConcourseApiVersion,
    ConcourseConfig,
)
from http_requests import AuthenticatedRequestBuilder

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


# Hard coded oauth user and password
# https://github.com/concourse/fly/blob/f4592bb32fe38f54018c2f9b1f30266713882c54/commands/login.go#L143
AUTH_TOKEN_REQUEST_USER = 'fly'
AUTH_TOKEN_REQUEST_PWD = 'Zmx5'


@functools.lru_cache()
@ensure_annotations
def from_cfg(concourse_cfg: ConcourseConfig, team_name: str, verify_ssl=False):
    '''
    Factory method to get Concourse API object
    '''
    base_url = concourse_cfg.ingress_url()
    cfg_factory = ci.util.ctx().cfg_factory()
    concourse_uam_cfg_name = concourse_cfg.concourse_uam_config()
    concourse_uam_cfg = cfg_factory.concourse_uam(concourse_uam_cfg_name)
    concourse_team = concourse_uam_cfg.team(team_name)
    team_name = concourse_team.teamname()
    username = concourse_team.username()
    password = concourse_team.password()
    concourse_version = concourse_cfg.concourse_version()

    request_builder = AuthenticatedRequestBuilder(
        basic_auth_username=AUTH_TOKEN_REQUEST_USER,
        basic_auth_passwd=AUTH_TOKEN_REQUEST_PWD,
        verify_ssl=verify_ssl
    )
    if concourse_version is ConcourseApiVersion.V5:
        routes = ConcourseApiRoutesV5(base_url=base_url, team=team_name)
        concourse_api = ConcourseApiV5(
            routes=routes,
            request_builder=request_builder,
            verify_ssl=verify_ssl,
        )
    else:
        raise NotImplementedError(
            "Concourse version {v} not supported".format(v=concourse_version.value)
        )

    concourse_api.login(
        username=username,
        passwd=password,
    )
    return concourse_api
