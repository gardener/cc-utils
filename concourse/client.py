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

import json
import warnings

from abc import abstractmethod
from ensure import ensure_annotations
from enum import Enum
from urllib.parse import urljoin, urlparse, urlencode
from urllib3.exceptions import InsecureRequestWarning

import functools
import sseclient
import util

from github.webhook import WebhookQueryAttributes
from model.concourse import (
    ConcourseTeamCredentials,
    ConcourseApiVersion,
    ConcourseConfig,
)
from http_requests import AuthenticatedRequestBuilder
from util import warning, not_empty

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
[2] https://github.com/concourse/atc/blob/master/routes.go
[3] https://www.getpostman.com/
'''


@functools.lru_cache()
def from_cfg(concourse_cfg: ConcourseConfig, team_name: str, verify_ssl=False):
    '''
    Factory method to get Concourse API object
    '''
    base_url = concourse_cfg.ingress_url()
    team_credentials = concourse_cfg.team_credentials(team_name)
    team_name = team_credentials.teamname()
    username = team_credentials.username()
    password = team_credentials.passwd()
    concourse_version = concourse_cfg.concourse_version()

    if concourse_version is ConcourseApiVersion.V3:
        routes = ConcourseApiRoutesV3(base_url=base_url, team=team_name)
        request_builder = AuthenticatedRequestBuilder(
                basic_auth_username=username,
                basic_auth_passwd=password,
                verify_ssl=verify_ssl
        )
        concourse_api = ConcourseApiV3(
            routes=routes,
            request_builder=request_builder,
            verify_ssl=verify_ssl,
        )
    elif concourse_version is ConcourseApiVersion.V4:
        routes = ConcourseApiRoutesV4(base_url=base_url, team=team_name)
        request_builder = AuthenticatedRequestBuilder(
                basic_auth_username=AUTH_TOKEN_REQUEST_USER,
                basic_auth_passwd=AUTH_TOKEN_REQUEST_PWD,
                verify_ssl=verify_ssl
        )
        concourse_api = ConcourseApiV4(
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


def select_attr(name: str):
    return lambda o: o.get(name)


class SetPipelineResult(Enum):
    UPDATED = 0
    CREATED = 1


# GLOBAL DEFINES
CONCOURSE_API_SUFFIX = 'api/v1'
# Hard coded oauth user and password
# https://github.com/concourse/fly/blob/f4592bb32fe38f54018c2f9b1f30266713882c54/commands/login.go#L143
AUTH_TOKEN_REQUEST_USER = 'fly'
AUTH_TOKEN_REQUEST_PWD = 'Zmx5'


class ConcourseApiRoutesBase(object):
    '''
    Constructs concourse REST API endpoint URLs for the given concourse base URL and
    team name.

    Not intended to be used outside of this module.
    '''
    @ensure_annotations
    def __init__(self, base_url: str, team: str, api_suffix=CONCOURSE_API_SUFFIX):
        '''
        @param base_url: the concourse URL as used to access the dashboard with a web browser
        @param team: the concourse team name for which to construct URLs
        @param api_suffix: specifies api version (currently only api/v1)
        '''
        self.base_url = base_url
        self.team = team
        self.api_suffix = api_suffix

    def _api_url(self, *parts, **kwargs):
        prefix_team = kwargs.get('prefix_team', True)
        team_name = self.team
        base_url = self.team_url(team_name) if prefix_team else \
                    urljoin(self.base_url, self.api_suffix)
        # preserve all parts of base url
        base_url +='/'

        return urljoin(base_url, '/'.join(parts))

    @ensure_annotations
    def team_url(self, team: str=None):
        if not team:
            team = self.team
        return self._api_url('teams', team, prefix_team=False)

    @abstractmethod
    def login(self):
        raise NotImplementedError

    def pipelines(self):
        return self._api_url('pipelines')

    def order_pipelines(self):
        return self._api_url('pipelines', 'ordering')

    @ensure_annotations
    def pipeline(self, pipeline_name: str):
        return self._api_url('pipelines', pipeline_name)

    @ensure_annotations
    def pipeline_cfg(self, pipeline_name: str):
        return self._api_url('pipelines', pipeline_name, 'config')

    @ensure_annotations
    def unpause_pipeline(self, pipeline_name: str):
        return self._api_url('pipelines', pipeline_name, 'unpause')

    @ensure_annotations
    def expose_pipeline(self, pipeline_name: str):
        return self._api_url('pipelines', pipeline_name, 'expose')

    @ensure_annotations
    def resource_check_webhook(
        self,
        pipeline_name: str,
        resource_name: str,
        query_attributes: WebhookQueryAttributes,
    ):
        base_url = self.resource_check(pipeline_name, resource_name)
        query = urlencode({
            WebhookQueryAttributes.WEBHOOK_TOKEN_ATTRIBUTE_NAME: query_attributes.webhook_token,
            WebhookQueryAttributes.CONCOURSE_ID_ATTRIBUTE_NAME: query_attributes.concourse_id,
            WebhookQueryAttributes.JOB_MAPPING_ID_ATTRIBUTE_NAME: query_attributes.job_mapping_id,
        })
        return util.urljoin(base_url, 'webhook?' + query)

    @ensure_annotations
    def resource_check(self, pipeline_name: str, resource_name: str):
        return self._api_url('pipelines', pipeline_name, 'resources', resource_name, 'check')

    @ensure_annotations
    def job_builds(self, pipeline_name: str, job_name: str):
        return self._api_url('pipelines', pipeline_name, 'jobs', job_name, 'builds')

    @ensure_annotations
    def job_build(self, pipeline_name: str, job_name: str, build_name: str):
        return self._api_url('pipelines', pipeline_name, 'jobs', job_name, 'builds', build_name)

    @ensure_annotations
    def build_events(self, build_id):
        return self._api_url('builds', str(build_id), 'events', prefix_team=False)

    @ensure_annotations
    def build_plan(self, build_id):
        return self._api_url('builds', str(build_id), 'plan', prefix_team=False)


class ConcourseApiRoutesV3(ConcourseApiRoutesBase):
    def login(self):
        return util.urljoin(
            self.base_url,
            'auth',
            'basic',
            'token' + '?' + urlencode({'team_name': self.team})
        )


class ConcourseApiRoutesV4(ConcourseApiRoutesBase):
    def login(self):
        return util.urljoin(
            self.base_url,
            'sky',
            'token'
        )


class ConcourseApiBase(object):
    '''
    Implements a subset of concourse REST API functionality.

    After creation, `login` ought to be invoked at least once to allow for the
    execution of requests that required autorization.

    @param base_url: concourse endpoint (e.g. https://ci.concourse.ci)
    @param team_name: the team name used for authentication
    @param verify_ssl: whether or not certificate validation is to be done
    '''
    @ensure_annotations
    def __init__(
        self,
        routes: ConcourseApiRoutesBase,
        request_builder: AuthenticatedRequestBuilder,
        verify_ssl=False,
    ):
        self.routes = routes
        self.request_builder = request_builder
        self.verify_ssl = verify_ssl

    @ensure_annotations
    def _get(self, url: str):
        return self.request_builder.get(url, return_type='json')

    @ensure_annotations
    def _put(self, url: str, body: str, headers={}, use_auth_token=True):
        return self.request_builder.put(url, body=body, headers=headers)

    @ensure_annotations
    def _post(self, url: str, body: str="", headers={}):
        return self.request_builder.post(url, body=body, headers=headers)

    @ensure_annotations
    def _delete(self, url: str):
        return self.request_builder.delete(url)

    @abstractmethod
    def login(self, team: str, username: str, passwd: str):
        raise NotImplementedError

    @ensure_annotations
    def set_pipeline(self, name: str, pipeline_definition):
        previous_version = self.pipeline_config_version(name)
        headers = {'x-concourse-config-version': previous_version}

        url = self.routes.pipeline_cfg(name)
        self._put(url, str(pipeline_definition), headers=headers)
        return SetPipelineResult.CREATED if previous_version is None else SetPipelineResult.UPDATED

    @ensure_annotations
    def delete_pipeline(self, name: str):
        url = self.routes.pipeline(pipeline_name=name)
        self._delete(url)

    def pipelines(self):
        pipelines_url = self.routes.pipelines()
        response = self._get(pipelines_url)
        return map(select_attr('name'), response)

    def order_pipelines(self, pipeline_names):
        url = self.routes.order_pipelines()
        self._put(url, json.dumps(pipeline_names))

    @ensure_annotations
    def pipeline_cfg(self, pipeline_name: str):
        pipeline_cfg_url = self.routes.pipeline_cfg(pipeline_name)
        response = self._get(pipeline_cfg_url)
        not_empty(response)
        return PipelineConfig(response, concourse_api=self, name=pipeline_name)

    def pipeline_resources(self, pipeline_names):
        if isinstance(pipeline_names, str):
            pipeline_names = [pipeline_names]

        resources = map(lambda name: self.pipeline_cfg(pipeline_name=name).resources, pipeline_names)
        for resource_list in resources:
            yield from resource_list

    @ensure_annotations
    def pipeline_config_version(self, pipeline_name: str):
        pipeline_cfg_url = self.routes.pipeline_cfg(pipeline_name)
        response = self.request_builder.get(
                pipeline_cfg_url,
                return_type=None,
                check_http_code=False
        )
        if response.status_code == 404:
            return None # pipeline did not exist yet

        # ensure we did receive an error other than 404
        self.request_builder._check_http_code(response, pipeline_cfg_url)

        return response.headers['X-Concourse-Config-Version']

    @ensure_annotations
    def unpause_pipeline(self, pipeline_name: str):
        unpause_url = self.routes.unpause_pipeline(pipeline_name)
        self.request_builder.put(
                unpause_url,
                body=""
        )

    @ensure_annotations
    def expose_pipeline(self, pipeline_name: str):
        expose_url = self.routes.expose_pipeline(pipeline_name)
        self.request_builder.put(
                expose_url,
                body="",
        )

    @ensure_annotations
    def job_builds(self, pipeline_name: str, job_name: str):
        '''
        Returns a list of Build objects for the specified job.
        The list is sorted by the build number, newest build last
        '''
        builds_url = self.routes.job_builds(pipeline_name, job_name)
        response = self._get(builds_url)
        builds = [Build(build_dict, self) for build_dict in response]
        builds = sorted(builds, key=lambda b: b.id())
        return builds

    @ensure_annotations
    def job_build(self, pipeline_name: str, job_name: str, build_name: str):
        build_url = self.routes.job_build(pipeline_name, job_name, build_name)
        response = self._get(build_url)
        return Build(response, self)

    @ensure_annotations
    def trigger_build(self, pipeline_name: str, job_name: str):
        trigger_url = self.routes.job_builds(pipeline_name, job_name)
        self._post(trigger_url)

    @ensure_annotations
    def build_plan(self, build_id):
        build_plan_url = self.routes.build_plan(build_id)
        response = self._get(build_plan_url)
        return BuildPlan(response, self)

    @ensure_annotations
    def build_events(self, build_id):
        build_plan_url = self.routes.build_events(build_id)
        # TODO: this request never seems to send an "EOF"
        # (probably to support streaming)
        # --> properly handle this special case
        response = self.request_builder.get(
                build_plan_url,
                return_type=None,
                stream=True # passed to sseclient
        )
        return BuildEvents(response, self)

    @ensure_annotations
    def trigger_resource_check(self, pipeline_name: str, resource_name: str):
        url = self.routes.resource_check(pipeline_name=pipeline_name, resource_name=resource_name)
        # Resource checks are triggered by a POST with an empty JSON-document as body against
        # the resource's check-url
        self._post(url, body='{}')


class ConcourseApiV3(ConcourseApiBase):
    @ensure_annotations
    def login(self, username: str, passwd: str):
        login_url = self.routes.login()
        response = self.request_builder.get(login_url, return_type='json')
        auth_token = response['value']
        self.request_builder = AuthenticatedRequestBuilder(
            auth_token=auth_token,
            verify_ssl=self.verify_ssl
        )
        return auth_token

    def set_team(self, team_credentials: ConcourseTeamCredentials):
        body = {}
        if team_credentials.has_basic_auth_credentials():
            basic_auth_cfg = {
                'username': team_credentials.username(),
                'password': team_credentials.passwd(),
            }
            body['auth'] = {'basicauth': basic_auth_cfg}
        if team_credentials.has_github_oauth_credentials():
            github_org, github_team = team_credentials.github_auth_team(split=True)
            github_cfg = {
                    'client_id': team_credentials.github_auth_client_id(),
                    'client_secret': team_credentials.github_auth_client_secret(),
                    'teams':
                    [{
                        'organization_name': github_org,
                        'team_name': github_team,
                    }],
            }
            if team_credentials.has_custom_github_auth_urls():
                github_cfg.update({
                    'auth_url': team_credentials.github_auth_auth_url(),
                    'token_url': team_credentials.github_auth_token_url(),
                    'api_url': team_credentials.github_auth_api_url(),
                })
            if 'auth' in body:
                body['auth'].update({'github': github_cfg})
            else:
                body['auth'] = {'github': github_cfg}

        team_url = self.routes.team_url(team_credentials.teamname())

        self._put(
          team_url,
          json.dumps(body)
        )


class ConcourseApiV4(ConcourseApiBase):
    def login(self, username: str, passwd: str):
        login_url = self.routes.login()
        form_data = "grant_type=password&password=" + passwd + \
                    "&scope=openid+profile+email+federated%3Aid+groups&username=" + username
        response = self._post(
            url=login_url,
            body=form_data,
            headers={"content-type": "application/x-www-form-urlencoded"}
        )
        auth_token = response.json()['access_token']
        self.request_builder = AuthenticatedRequestBuilder(
            auth_token=auth_token,
            verify_ssl=self.verify_ssl
        )
        return auth_token

    def set_team(self, team_credentials: ConcourseTeamCredentials):
        body = {}
        body['auth'] = {
            "users": [
                "local:" + team_credentials.username()
            ]
        }
        if team_credentials.has_github_oauth_credentials():
            body['auth'].update({
                "groups": [
                    "github:" + team_credentials.github_auth_team()
                ]
            })

        team_url = self.routes.team_url(team_credentials.teamname())
        self._put(team_url, json.dumps(body))


class ModelBase(object):
    '''
    Base class for Concourse model classes

    Not intended to be instantiated by users of this module
    '''

    def __init__(self, raw_dict: dict, concourse_api:ConcourseApiBase):
        self.api = concourse_api
        self.raw_dict = raw_dict


class PipelineConfig(object):
    '''
    Wrapper around the dictionary received from invoking the concourse
    `pipelines/<pipeline>/config` REST API

    Not intended to be instantiated by users of this module
    '''
    @ensure_annotations
    def __init__(self, raw_dict: dict, concourse_api: ConcourseApiBase, name: str):
        self.concourse_api = concourse_api
        self.name = name
        self.raw_dict = raw_dict['config']
        resources = self.raw_dict.get('resources', None)
        if not resources:
            warning('Pipeline did not contain resource definitions: {p}'.format(p=name))
            raise ValueError()
        self.resources = map(lambda r: Resource(r, self), resources)

    def resources_of_types(self, types):
        return filter(lambda r: r.type in types, self.resources)


class Resource(object):
    '''
    Wrapper around the dictionary representing a resource as part of a
    concourse.PipelineConfig

    Not intended to be instantiated by users of this module
    '''
    @ensure_annotations
    def __init__(self, raw_dict:dict, pipeline:PipelineConfig):
        self.pipeline = pipeline
        self.concourse_api = pipeline.concourse_api
        self.raw = raw_dict
        self.type = raw_dict['type']
        self.source = raw_dict['source']
        self.name = raw_dict['name']

    def has_webhook_token(self):
        return 'webhook_token' in self.raw and len(self.webhook_token()) > 0

    def webhook_token(self):
        return self.raw['webhook_token']

    def github_source(self):
        return GithubSource(self.source, self.concourse_api)

    def __str__(self):
        return 'Concourse Resource {n}. Type: {t}, webhook_token: {wht}'.format(
            n=self.name,
            t=self.type,
            wht=self.webhook_token(),
        )


class GithubSource(object):
    '''
    Wrapper around the source attribute of a concourse.Resource instance in
    the special case said resource is a "githubby" resource (either a git
    repository or a github-pull-request)

    Not intended to be instantiated by users of this module
    '''
    @ensure_annotations
    def __init__(self, raw_dict:dict, concourse_api:ConcourseApiBase):
        self.concourse_api = concourse_api
        self.raw = raw_dict
        self.uri = raw_dict['uri']

    def repo_path(self):
        return urlparse(self.uri).path

    def parse_organisation(self):
        path = self.repo_path()
        # hardcode assumption: first part always denotes organisation
        return path.split('/')[1]

    def parse_repository(self):
        path = self.repo_path()
        # hardcode assumption: second part always denotes organisation
        return path.split('/')[2]

    def hostname(self):
        return urlparse(self.uri).hostname

    def access_token(self):
        return self.raw['access_token']


class Build(ModelBase):
    '''
    Wrapper around the dictionary representing a build.

    Not intended to be instantiated by users of this module
    '''

    def id(self):
        return int(self.raw_dict.get('id'))

    def start_time(self):
        return int(self.raw_dict.get('start_time'))

    def stop_time(self):
        return int(self.raw_dict.get('end_time'))

    def status(self):
        return BuildStatus(self.raw_dict.get('status'))

    def plan(self):
        return self.api.build_plan(self.id())

    def events(self):
        return self.api.build_events(self.id())


class BuildPlan(ModelBase):
    def task_id(self, task_name: str):
        '''
        determines the task-id for the given task_name
        If the task_name is not unique, the task-id for the first-found task with
        the given name is returned.
        If no task with the given name is found, `None` is returned.
        '''
        plan = self.raw_dict.get('plan')

        def find_tid(p):
            if 'task' in p:
                task = p.get('task')
                if task.get('name') == task_name:
                    return p.get('id') # end recursion

            for k, v in p.items():
                # recursively traverse plan dict
                if isinstance(v, dict):
                    task_id = find_tid(v)
                    if task_id:
                        return task_id
                if isinstance(v, list):
                    for element in v:
                        task_id = find_tid(element)
                        if task_id:
                            return task_id
        return find_tid(plan)


class BuildEvents(object):
    '''
    Wrapper around the event stream returned by concourse when querying the events for a
    certain build execution. The event stream is consumed using the `process_events`
    method.

    Not intended to be instantiated by users of this module
    '''

    def __init__(self, response, concourse_api):
        '''
        @param response: the unprocessed reponse object as returned from the request.
                         concourse will send an event stream (server-side events),
                         so we have to use an appropriate client to consume them
        '''
        self.api = concourse_api
        self.response = response

    def process_events(self, callback=None, filter_for_task_id=None, yield_cb=False):
        '''
        processes all received streaming events in a blocking manner until the
        'finish-task' event is reached, which marks the end of a build execution.

        An optional callback may be specified, which is called for each received event
        with the parsed event data (wrapped into a dictionary).

        @param callback: callable accepting exactly one positional argument
        '''
        client = sseclient.SSEClient(self.response)
        should_stop = False
        # pylint: disable=no-member
        # events attrib is added by response
        for event in client.events():
            if event is None or not event.data or len(event.data.strip()) == 0:
                return True
            parsed = json.loads(event.data)
            data = parsed.get('data')

            if not data:
                continue

            if filter_for_task_id:
                if data.get('origin') and data['origin'].get('id') == filter_for_task_id:
                    matches_task_filter = True
                else:
                    matches_task_filter = False
            else:
                matches_task_filter = True

            if matches_task_filter and parsed.get('event') == 'finish-task':
                should_stop = True # do not wait any longer as our task has finished

            if callback and matches_task_filter:
                result = callback(data)
                if result and yield_cb:
                    yield result

            # if 'finish-task' event is reached, we always want to stop
            if not should_stop and data.get('event') == 'end':
                should_stop = True

            if should_stop:
                client.close()
                return True
        # pylint: enable=no-member

    def iter_buildlog(self, task_id: str):
        '''
        returns an iterator yielding the build-log for the task identified by the given task_id.
        Task IDs may be retrieved from `BuildPlan#task_id`.
        '''
        def filter_log(log_data):
            if (
                not log_data.get('origin') or
                not log_data.get('payload') or
                log_data['origin'].get('id') != task_id
            ):
                return
            return log_data['payload']

        def stop_if_task_ended(event_data):
            if not event_data.event or event_data.event != 'finish-task':
                return False
            return True

        yield from self.process_events(
            callback=filter_log,
            filter_for_task_id=task_id,
            yield_cb=True
        )


class BuildStatus(Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ERRORED = "errored"
    RUNNING = "started"
    ABORTED = "aborted"
