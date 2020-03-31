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

import json

from ensure import ensure_annotations
from enum import Enum
from urllib.parse import urlparse

import sseclient

from ci.util import warning


class SetPipelineResult(Enum):
    UPDATED = 0
    CREATED = 1


class ModelBase(object):
    '''
    Base class for Concourse model classes

    Not intended to be instantiated by users of this module
    '''

    def __init__(self, raw:dict, concourse_api):
        self.api = concourse_api
        self.raw = raw


class ResourceVersion(ModelBase):
    '''
    Wraps a single result returned from concourse's `<resource>/versions` route.
    Both `metadata` and `version` adhere to a schema specific to the resource type.
    '''
    def type(self):
        return self.raw['type']

    def version(self) -> dict:
        return self.raw['version'] # specific to resource type

    def metadata(self) -> dict:
        return self.raw['metadata'] # specific to resource type

    def enabled(self) -> bool:
        return self.raw['enabled']


class PipelineConfig(object):
    '''
    Wrapper around the dictionary received from invoking the concourse
    `pipelines/<pipeline>/config` REST API

    Not intended to be instantiated by users of this module
    '''
    @ensure_annotations
    def __init__(self, raw:dict, concourse_api, name:str):
        self.concourse_api = concourse_api
        self.name = name
        self.raw = raw['config']
        resources = self.raw.get('resources', None)
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
    def __init__(self, raw:dict, pipeline:PipelineConfig):
        self.pipeline = pipeline
        self.concourse_api = pipeline.concourse_api
        self.raw = raw
        self.type = raw['type']
        self.source = raw['source']
        self.name = raw['name']

    def has_webhook_token(self):
        return 'webhook_token' in self.raw and len(self.webhook_token()) > 0

    def webhook_token(self):
        return self.raw['webhook_token']

    def pipeline_name(self):
        return self.pipeline.name

    def github_source(self):
        return GithubSource(self.source, self.concourse_api)

    def failing_to_check(self):
        return self.raw.get('failing_to_check', False)

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
    def __init__(self, raw:dict, concourse_api):
        self.concourse_api = concourse_api
        self.raw = raw
        self.uri = raw['uri']

    def team_name(self):
        return self.raw['team_name']

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

    def branch_name(self):
        return self.raw['branch']

    def access_token(self):
        return self.raw['access_token']

    def disable_ci_skip(self) -> bool:
        return self.raw.get('disable_ci_skip')


class Build(ModelBase):
    '''
    Wrapper around the dictionary representing a build.

    Not intended to be instantiated by users of this module
    '''

    def id(self):
        return int(self.raw.get('id'))

    def start_time(self):
        return int(self.raw.get('start_time'))

    def stop_time(self):
        return int(self.raw.get('end_time'))

    def build_number(self) -> str:
        return self.raw.get('name')

    def status(self):
        return BuildStatus(self.raw.get('status'))

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
        plan = self.raw.get('plan')

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


class Worker(ModelBase):
    '''
    Wrapper around the dictionary representing a Concourse Worker.
    Not intended to be instantiated by users of this module
    '''
    def state(self):
        return self.raw['state']

    def name(self):
        return self.raw['name']


class BuildStatus(Enum):
    ABORTED = "aborted"
    ERRORED = "errored"
    FAILED = "failed"
    PENDING = "pending"
    RUNNING = "started"
    SUCCEEDED = "succeeded"
