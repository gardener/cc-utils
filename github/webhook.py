# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
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

from ensure import ensure_annotations
from github3.exceptions import NotFoundError
from github3.github import GitHub
from github3.orgs import OrganizationHook
from github3.orgs import Organization
from urllib.parse import urlparse

DEFAULT_HOOK_EVENTS = ['*'] # "send everything"
DEFAULT_HOOK_NAME = 'web' # see https://developer.github.com/v3/repos/hooks/
DEFAULT_HOOK_CONTENT_TYPE = 'json'


class WebhookQueryAttributes(object):
    WHD_ID_ATTRIBUTE_NAME = 'whd_id'

    def __init__(
        self,
        whd_id: str,
    ):
        self.whd_id = whd_id


class GithubWebHookSyncer(object):
    '''
    Synchronises web hooks for organisations hosted on a github instance.
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
    def _retrieve_existing_hook_or_none(
        self,
        organization:Organization,
        hook_name:str,
        callback_url:str
    ):
        '''
        @raises github3.exceptions.NotFoundError in case of missing privileges to enumerate webhooks
        '''
        hooks = filter(lambda h: h.name == hook_name, organization.hooks())
        hooks = filter(lambda h: self._has_similar_url(h, callback_url), hooks)
        hooks = list(hooks)
        if len(hooks) == 1:
            return hooks[0]
        elif len(hooks) == 0:
            return None
        raise RuntimeError('found two similar webhooks - what to do now?')

    @ensure_annotations
    def _create_hook(
        self,
        organization:Organization,
        callback_url:str,
        hook_name:str=DEFAULT_HOOK_NAME,
        content_type:str=DEFAULT_HOOK_CONTENT_TYPE,
        events:list=DEFAULT_HOOK_EVENTS,
        active:bool=True
    ):
        # see https://developer.github.com/v3/repos/hooks/
        config = {
            'url': callback_url,
            'content_type': content_type,
        }

        hook = organization.create_hook(
            name=hook_name,
            config=config,
            active=active
        )

        if not hook:
            raise RuntimeError('failed to create webhook {n} for organization {r}'.format(
                n=hook_name,
                r=str(organization)
            )
        )
        return hook

    @ensure_annotations
    def _requires_update(
        self,
        hook:OrganizationHook,
        callback_url:str,
        events:list,
        active:bool=True
    ):
        # early exit on first found difference
        if hook.url != callback_url:
            return True
        if set(hook.events) != set(events):
            return True
        if hook.active != active:
            return True
        return False

    @ensure_annotations
    def _has_similar_url(
        self,
        hook:OrganizationHook,
        callback_url:str
    ):
        # we should update in case only the URL params or schema changed
        hook_url = urlparse(hook.config['url'])
        cb_url = urlparse(callback_url)

        if hook_url.netloc == cb_url.netloc and hook_url.path == cb_url.path:
            return True
        return False

    @ensure_annotations
    def add_or_update_hooks(
        self,
        organization_name:str,
        callback_urls, # List[str]
        hook_name:str=DEFAULT_HOOK_NAME,
        events:list=DEFAULT_HOOK_EVENTS,
        content_type:str=DEFAULT_HOOK_CONTENT_TYPE,
        active:bool=True,
        skip_ssl_validation=False
    ):
        '''
        convenience wrapper for add_or_update_hook, processing an iterable of callback_urls
        '''
        for callback_url in callback_urls:
            self.add_or_update_hook(
                organization_name=organization_name,
                callback_url=callback_url,
                hook_name=hook_name,
                events=events,
                content_type=content_type,
                active=active,
                skip_ssl_validation=skip_ssl_validation,
            )

    @ensure_annotations
    def add_or_update_hook(
        self,
        organization_name:str,
        callback_url:str,
        hook_name:str=DEFAULT_HOOK_NAME,
        events:list=DEFAULT_HOOK_EVENTS,
        content_type:str=DEFAULT_HOOK_CONTENT_TYPE,
        active:bool=True,
        skip_ssl_validation=False
    ):
        '''
        Idempotently ensures that the specified webhook configuration is set
        for the given git organization. If the webhook is absent, it is created.
        If it exists, it is reconfigured according to the specified configuration
        in case differences are detected.

        See https://developer.github.com/webhooks/

        @param organization_name: organization name
        @param callback_urls: URLs the webhook should call
        @param hook_name: the webhook's name
        @param events: the events for which the webhook should trigger
        @param content_type: webhook content type (see github webhook documentation)
        @param active: whether the webhook should be active
        '''
        organization = self.github.organization(
            username=organization_name,
        )
        if not organization:
            raise RuntimeError(
                f'failed to access "{organization_name}". Verify credentials and organization name'
            )
        try:
            hook = self._retrieve_existing_hook_or_none(
                organization=organization,
                hook_name=hook_name,
                callback_url=callback_url
            )
        except NotFoundError:
            raise RuntimeError(
                f'failed to retrieve webhooks for "{organization_name}". Verify credentials'
            )
        # create_hook requires additional parameter 'name'
        hook_kwargs = {}

        if not hook:
            create_or_update = organization.create_hook
            hook_kwargs['name'] = hook_name
        else:
            # early-exit if up-to-date
            if not self._requires_update(
                hook=hook,
                callback_url=callback_url,
                events=events,
                active=active
            ):
                return
            create_or_update = hook.edit

        # see https://developer.github.com/v3/repos/hooks/
        config = {
            'url': callback_url,
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
                f'failed to update or create webhook for "{organization_name}"'
            )

    def remove_outdated_hooks(
        self,
        organization_name:str,
        urls_to_keep,
        url_filter_fun,
    ):
        organization = self.github.organization(
            username=organization_name
        )

        removed = 0
        for hook in organization.hooks():
            url = hook.config.get('url')
            if not url:
                continue # strangely, sometimes webhooks do not have a callback url
            if url in urls_to_keep:
                continue
            elif not url_filter_fun(url):
                continue
            else:
                hook.delete()
                removed +=1

        return removed
