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

import ci.util


class Routes:
    '''
    github-api-v3-routes missing from github3.py
    '''
    def __init__(self, github_api):
        self._base_url = github_api._github_url

    def _url(self, *parts):
        return ci.util.urljoin(self._base_url, *parts)

    def org(self, name: str):
        return self._url('orgs', name)

    def org_hooks(self, name: str):
        return ci.util.urljoin(self.org(name=name), 'hooks')

    def org_hook(self, name: str, id: str):
        return ci.util.urljoin(self.org_hooks(name=name), id)

    def org_hook_deliveries(self, name: str, id: str):
        return ci.util.urljoin(self.org_hook(name=name, id=id), 'deliveries')

    def org_hook_delivery_atttemps(self, name: str, hook_id: str, delivery_id: str):
        return ci.util.urljoin(
            self.org_hook_deliveries(name=name,id=hook_id),
            delivery_id,
            'attempts',
        )


def org_webhooks(github_api, org_name: str) -> list[dict]:
    '''
    returns webhooks for given github-org
    elements are dicts with attributes: {
        type,
        id,
        name,
        active,
        events,
        config,
        updated_at,
        created_at,
        url,
        ping_url,
        deliveries_url,
    }
    see: https://docs.github.com/en/rest/reference/orgs#webhooks
    '''
    routes = Routes(github_api=github_api)

    return github_api._get(routes.org_hooks(name=org_name))
