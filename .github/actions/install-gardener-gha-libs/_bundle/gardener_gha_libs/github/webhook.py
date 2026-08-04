# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


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
