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

import functools
import urllib.parse

import github3
import github3.github

import http_requests
import model
import util

if util._running_on_ci():
    log_github_access = True
else:
    log_github_access = False


def github_api_ctor(github_url: str, verify_ssl: bool=True):
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

    if hostname.lower() == 'github.com':
        return github3.github.GitHub
    else:
        return functools.partial(github3.github.GitHubEnterprise, url=github_url, verify=verify_ssl)


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

    session = http_requests.mount_default_adapter(github_api.session)

    if log_github_access:
        session.hooks['response'] = http_requests.log_stack_trace_information

    return github_api
