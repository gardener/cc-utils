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

from ensure import ensure_annotations
from github3.exceptions import NotFoundError
from github3.github import GitHub
from github3.orgs import OrganizationHook
from github3.orgs import Organization
from urllib.parse import urlparse, parse_qs

DEFAULT_ORG_HOOK_EVENTS = ['*'] # send everything
DEFAULT_ORG_HOOK_NAME = 'web' # see https://developer.github.com/v3/orgs/hooks/
DEFAULT_ORG_HOOK_CONTENT_TYPE = 'json'
DEFAULT_ORG_HOOK_QUERY_KEY = 'whd_config_name'


class GithubWebHookSyncer(object):
    '''
    Synchronises web hooks for repositories hosted on a github instance.
    '''
    @ensure_annotations
    def __init__(self, github: GitHub):
        '''
        The passed instance of github is used to perform operations against it.
        Must be initialised with a valid URL and appropriate credentials
        granting the necessary privileges.

        @param github: an initialised instance of github3.github.GitHub
        '''
        self.github = github

    @ensure_annotations
    def create_or_update_org_hook(
        self,
        organization_name: str,
        webhook_url: str,
        hook_name: str=DEFAULT_ORG_HOOK_NAME,
        events: list=DEFAULT_ORG_HOOK_EVENTS,
        content_type: str=DEFAULT_ORG_HOOK_CONTENT_TYPE,
        active: bool=True,
        skip_ssl_validation=False
    ):
        organization = self.github.organization(
            username=organization_name,
        )
        if not organization:
            raise RuntimeError(
                f'failed to access "{organization_name}". Verify credentials and organization name'
            )

        try:
            hook = self._retrieve_existing_org_hook_or_none(
                organization=organization,
                hook_name=hook_name,
                webhook_url=webhook_url
            )
        except NotFoundError:
            raise RuntimeError(
                f'failed to retrieve webhooks for "{organization_name}". Verify credentials'
            )

        hook_kwargs = {}
        if not hook:
            create_or_update = organization.create_hook
            hook_kwargs['name'] = hook_name
        else:
            create_or_update = hook.edit

        config = {
            'url': webhook_url,
            'content_type': content_type,
            'insecure_ssl': '1' if skip_ssl_validation else '0'
        }

        result = create_or_update(
            config=config,
            events=events,
            active=active,
            **hook_kwargs
        )

        if not result:
            raise RuntimeError(
                'failed to update or create org webhook for {o}'.format(
                    o=organization,
                )
            )

    @ensure_annotations
    def _retrieve_existing_org_hook_or_none(
        self,
        organization: Organization,
        hook_name: str,
        webhook_url: str
    ):
        '''
        @raises github3.exceptions.NotFoundError in case of missing privileges to enumerate webhooks
        '''
        def _webhook_set_by_us(org_hook: OrganizationHook, webhook_url: str):
            org_hook_url = urlparse(org_hook.config['url'])
            org_hook_query_key = parse_qs(org_hook_url.query).get(
                DEFAULT_ORG_HOOK_QUERY_KEY
            )
            if not org_hook_query_key:
                return False

            webhook_url = urlparse(webhook_url)
            if org_hook_url.path != webhook_url.path:
                return False

            return True

        hooks = filter(lambda org_hook: org_hook.name == hook_name, organization.hooks())
        hooks = filter(lambda org_hook: _webhook_set_by_us(org_hook, webhook_url), hooks)
        hooks = list(hooks)
        if len(hooks) == 1:
            return hooks[0]
        elif len(hooks) == 0:
            return None
        raise RuntimeError('found two similar webhooks - what to do now?')
