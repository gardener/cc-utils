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

import datetime
import dataclasses
import enum
import functools
import traceback
import typing
import urllib.parse

import cachecontrol
import gci.componentmodel as cm
import github3
import github3.github
import github3.session

import ccc.elasticsearch
import ci.util
import github.util
import http_requests
import model
import product.model

if ci.util._running_on_ci():
    log_github_access = False
else:
    log_github_access = False


class SessionAdapter(enum.Enum):
    NONE = None
    RETRY = 'retry'
    CACHE = 'cache'


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
    session_adapter = SessionAdapter(session_adapter)
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
        session.hooks['response'] = log_stack_trace_information_hook

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


def repo_helper(
    host: str,
    org: str,
    repo: str,
    branch: str='master',
    session_adapter: SessionAdapter=SessionAdapter.RETRY,
):
    api = github_api(
        github_cfg=github_cfg_for_hostname(host_name=host),
        session_adapter=session_adapter,
    )

    return github.util.GitHubRepositoryHelper(
        owner=org,
        name=repo,
        github_api=api,
        default_branch=branch,
    )


def pr_helper(
    host: str,
    org: str,
    repo: str,
    session_adapter: SessionAdapter=SessionAdapter.RETRY,
):
    api = github_api(
        github_cfg=github_cfg_for_hostname(host_name=host),
        session_adapter=session_adapter,
    )

    return github.util.PullRequestUtil(
        owner=org,
        name=repo,
        github_api=api,
    )


# XXX remove this alias again
github_repo_helper = repo_helper


@functools.lru_cache()
def github_api(
    github_cfg: 'model.GithubConfig',
    session_adapter: SessionAdapter=SessionAdapter.RETRY,
):
    github_url = github_cfg.http_url()
    github_auth_token = github_cfg.credentials().auth_token()

    verify_ssl = github_cfg.tls_validation()

    github_ctor = github_api_ctor(
        github_url=github_url, verify_ssl=verify_ssl,
        session_adapter=SessionAdapter.RETRY,
    )
    github_api = github_ctor(
        token=github_auth_token,
    )

    if not github_api:
        ci.util.fail("Could not connect to GitHub-instance {url}".format(url=github_url))

    return github_api


@functools.lru_cache()
def github_cfg_for_hostname(
    host_name,
    cfg_factory=None,
    require_labels=('ci',), # XXX unhardcode label
):
    ci.util.not_none(host_name)
    if not cfg_factory:
        ctx = ci.util.ctx()
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


def log_stack_trace_information_hook(resp, *args, **kwargs):
    '''
    This function stores the current stacktrace in elastic search.
    It must not return anything, otherwise the return value is assumed to replace the response
    '''
    if not ci.util._running_on_ci():
        return # early exit if not running in ci job

    config_set_name = ci.util.check_env('CONCOURSE_CURRENT_CFG')
    try:
        els_index = 'github_access_stacktrace'
        try:
            config_set = ci.util.ctx().cfg_factory().cfg_set(config_set_name)
        except KeyError:
            # do nothing: external concourse does not have config set 'internal_active'
            return
        elastic_cfg = config_set.elasticsearch()

        now = datetime.datetime.utcnow()
        json_body = {
            'date': now.isoformat(),
            'url': resp.url,
            'req_method': resp.request.method,
            'stacktrace': traceback.format_stack()
        }

        elastic_client = ccc.elasticsearch.from_cfg(elasticsearch_cfg=elastic_cfg)
        elastic_client.store_document(
            index=els_index,
            body=json_body
        )

    except Exception as e:
        ci.util.info(f'Could not log stack trace information: {e}')


def github_api_from_component(component: typing.Union[cm.Component, product.model.Component]):
    if isinstance(component, product.model.Component):
        github_cfg = github_cfg_for_hostname(host_name=component.github_host())
        return github_api(github_cfg=github_cfg)
    elif isinstance(component, cm.Component):
        source = _get_single_repo(component=component)

        host_name = source.access.repoUrl.split('/')[0]

        github_cfg = github_cfg_for_hostname(host_name=host_name)
        return github_api(github_cfg=github_cfg)
    else:
        raise ValueError


@dataclasses.dataclass
class GithubRepo:
    host_name: str
    org_name: str
    repo_name: str

    @staticmethod
    def from_component(component: cm.Component):
        source = _get_single_repo(component)
        host, org, repo = source.access.repoUrl.split('/')
        return GithubRepo(host,org,repo)


def _get_single_repo(component: cm.Component):
    if len(component.sources) != 1:
        raise NotImplementedError

    source = component.sources[0]
    if source.type is not cm.SourceType.GIT:
        raise NotImplementedError

    if source.access.type is not cm.AccessType.GITHUB:
        raise NotImplementedError

    return source
