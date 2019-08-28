# Copyright (c) 2019 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
# under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

import enum
import functools
import urllib.parse

import cachecontrol
import github3
import github3.github
import github3.session

import http_requests
import model
import util

if util._running_on_ci():
    log_github_access = True
else:
    log_github_access = False


class SessionAdapter(enum.Enum):
    NONE = enum.auto()
    RETRY = enum.auto()
    CACHE = enum.auto()


def github_api_ctor(
    github_url: str,
    verify_ssl: bool=True,
    session_adapter: SessionAdapter=SessionAdapter.RETRY,
):
    '''returns the appropriate github3.GitHub constructor for the given github URL

    In case github_url does not refer to github.com, the c'tor for GithubEnterprise is
    returned with the url argument preset, thus disburdening users to differentiate
    between github.com and non-github.com cases.
    '''
    parsed = urllib.parse.urlparse(github_url)
    if parsed.scheme:
        hostname = parsed.hostname
    else:
        raise ValueError('failed to parse url: ' + str(github_url))

    session = github3.session.GitHubSession()
    if session_adapter is SessionAdapter.NONE:
        pass
    elif session_adapter is SessionAdapter.RETRY:
        session = http_requests.mount_default_adapter(session)
    elif session_adapter is SessionAdapter.CACHE:
        session = cachecontrol.CacheControl(
            session,
            cache_etags=True,
        )
    else:
        raise NotImplementedError

    if log_github_access:
        session.hooks['response'] = http_requests.log_stack_trace_information

    if hostname.lower() == 'github.com':
        return functools.partial(
            github3.github.GitHub,
            session=session,
        )
    else:
        return functools.partial(
            github3.github.GitHubEnterprise,
            url=github_url,
            verify=verify_ssl,
            session=session,
        )


@functools.lru_cache()
def github_api(
    github_cfg: 'model.GithubConfig',
):
    github_url = github_cfg.http_url()
    github_auth_token = github_cfg.credentials().auth_token()

    verify_ssl = github_cfg.tls_validation()

    github_ctor = github_api_ctor(github_url=github_url, verify_ssl=verify_ssl)
    github_api = github_ctor(
        token=github_auth_token,
    )

    if not github_api:
        util.fail("Could not connect to GitHub-instance {url}".format(url=github_url))

    return github_api


@functools.lru_cache()
def github_cfg_for_hostname(
    host_name,
    cfg_factory=None,
    require_labels=('ci',), # XXX unhardcode label
):
    util.not_none(host_name)
    if not cfg_factory:
        ctx = util.ctx()
        cfg_factory = ctx.cfg_factory()

    if isinstance(require_labels, str):
        require_labels = tuple(require_labels)

    def has_required_labels(github_cfg):
        for required_label in require_labels:
            if required_label not in github_cfg.purpose_labels():
                return False
        return True

    for github_cfg in filter(has_required_labels, cfg_factory._cfg_elements(cfg_type_name='github')):
        if github_cfg.matches_hostname(host_name=host_name):
            return github_cfg

    raise RuntimeError(f'no github_cfg for {host_name} with {require_labels}')
