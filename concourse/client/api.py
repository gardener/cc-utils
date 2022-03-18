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

import enum
import json
import logging
import typing

import requests
from ensure import ensure_annotations

import ci.log
from concourse.client.model import ResourceType
from http_requests import AuthenticatedRequestBuilder

from concourse.client.model import (
    Build,
    BuildEvents,
    BuildPlan,
    ConcourseJob,
    PipelineConfig,
    PipelineConfigResource,
    PipelineResource,
    ResourceVersion,
    SetPipelineResult,
    Worker,
)
from .routes import ConcourseApiRoutesBase, ConcourseApiRoutesV6_3_0


ci.log.configure_default_logging()
logger = logging.getLogger(__name__)

# Hard coded oauth user and password
# https://github.com/concourse/fly/blob/f4592bb32fe38f54018c2f9b1f30266713882c54/commands/login.go#L143
AUTH_TOKEN_REQUEST_USER = 'fly'
AUTH_TOKEN_REQUEST_PWD = 'Zmx5'


class ConcourseApiVersion(enum.Enum):
    V6_3_0 = '6.3.0'
    V6_5_1 = '6.5.1'


latest_concourse_api_version = ConcourseApiVersion.V6_5_1


class ConcourseApiFactory:
    '''Factory for ConcourseApi objects
    '''
    @staticmethod
    def create_api(
        base_url: str,
        team_name: str,
        verify_ssl: str,
        concourse_api_version: latest_concourse_api_version,
    ) -> 'ConcourseApiBase':
        # disable logging output for now (breaks template in lss)
        # logger.info(f'created client w/ {base_url=}')

        if not concourse_api_version:
            concourse_api_version = latest_concourse_api_version

        if concourse_api_version is ConcourseApiVersion.V6_5_1:
            routes = ConcourseApiRoutesV6_3_0(base_url=base_url, team=team_name)
            return ConcourseApiV6_5_1(
                routes=routes,
                verify_ssl=verify_ssl,
            )
        else:
            raise NotImplementedError(
                f'not supported: {concourse_api_version=}',
            )


def select_attr(name: str):
    return lambda o: o.get(name)


class ConcourseApiBase:
    '''
    Implements a subset of concourse REST API functionality.

    After creation, `login` ought to be invoked at least once to allow for the
    execution of requests that required autorization.

    @param base_url: concourse endpoint (e.g. https://ci.concourse.ci)
    @param team_name: the team name used for authentication
    @param verify_ssl: whether or not certificate validation is to be done
    '''

    AUTH_TOKEN_ATTRIBUTE_NAME = 'access_token'

    @ensure_annotations
    def __init__(
        self,
        routes: ConcourseApiRoutesBase,
        request_builder: AuthenticatedRequestBuilder=None,
        verify_ssl=False,
    ):
        if request_builder:
            logger.warning('passing-in requests-builder is deprecated')

        self.routes = routes
        self.request_builder = None
        self.verify_ssl = verify_ssl

    @ensure_annotations
    def _get(self, url: str):
        if not self.request_builder:
            raise ValueError('login first')

        return self.request_builder.get(url, return_type='json')

    @ensure_annotations
    def _put(self, url: str, body: str, headers={}, use_auth_token=True):
        if not self.request_builder:
            raise ValueError('login first')

        return self.request_builder.put(url, body=body, headers=headers)

    @ensure_annotations
    def _post(self, url: str, body: str="", headers={}):
        if not self.request_builder:
            raise ValueError('login first')

        return self.request_builder.post(url, body=body, headers=headers)

    @ensure_annotations
    def _delete(self, url: str):
        if not self.request_builder:
            raise ValueError('login first')

        return self.request_builder.delete(url)

    def login(self, username: str, passwd: str):
        login_url = self.routes.login()
        form_data = {
            'grant_type': 'password',
            'password': passwd,
            'scope': 'openid profile email federated:id groups',
            'username': username,
        }

        response = requests.post(
            url=login_url,
            data=form_data,
            auth=(AUTH_TOKEN_REQUEST_USER, AUTH_TOKEN_REQUEST_PWD),
            headers={'content-type': 'application/x-www-form-urlencoded'}
        )
        response.raise_for_status()
        auth_token = response.json()[self.AUTH_TOKEN_ATTRIBUTE_NAME]
        self.request_builder = AuthenticatedRequestBuilder(
            auth_token=auth_token,
            verify_ssl=self.verify_ssl
        )
        return auth_token

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
        return PipelineConfig(response, concourse_api=self, name=pipeline_name)

    def pipeline_resources(
        self,
        pipeline_names: typing.Union[typing.Sequence[str], str],
        resource_type: typing.Optional[ResourceType]=None,
    ) -> typing.Generator[PipelineConfigResource, None, None]:
        if isinstance(pipeline_names, str):
            pipeline_names = [pipeline_names]

        resources = (self.pipeline_cfg(pipeline_name=name).resources for name in pipeline_names)
        for resource_list in resources:
            for resource in resource_list:
                resource: PipelineConfigResource
                if not resource_type or ResourceType(resource.type) is resource_type:
                    yield resource

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
    def pause_pipeline(self, pipeline_name: str):
        pause_url = self.routes.pause_pipeline(pipeline_name)
        self.request_builder.put(
                pause_url,
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
    def pause_job(self, pipeline_name: str, job_name: str):
        pause_url = self.routes.pause_job(pipeline_name, job_name)
        self.request_builder.put(
                pause_url,
                body=""
        )

    @ensure_annotations
    def unpause_job(self, pipeline_name: str, job_name: str):
        unpause_url = self.routes.unpause_job(pipeline_name, job_name)
        self.request_builder.put(
                unpause_url,
                body=""
        )

    @ensure_annotations
    def trigger_build(self, pipeline_name: str, job_name: str):
        trigger_url = self.routes.job_builds(pipeline_name, job_name)
        response = self._post(trigger_url)
        return Build(response.json(), self)

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
    def trigger_resource_check(self, pipeline_name: str, resource_name: str, retries:int=5):
        url = self.routes.resource_check(pipeline_name=pipeline_name, resource_name=resource_name)

        # Resource checks are triggered by a POST with an empty JSON-document as body against
        # the resource's check-url
        response = self.request_builder.post(url, check_http_code=False, body='{}', headers={})

        if response.ok:
            return

        if response.status_code == 500 and response.reason.startswith('parent type has no version'):
            if retries > 0:
                self.trigger_resource_check(pipeline_name, resource_name, retries-1)
            else:
                response.reason = (
                    f"Unable to check resource '{resource_name}' in pipeline "
                    f"'{pipeline_name}'. If this pipeline was recently deployed, please "
                    "try again in a minute."
                )
                response.raise_for_status()
        else:
            response.raise_for_status()

    @ensure_annotations
    def resource(self, pipeline_name: str, resource_name: str):
        url = self.routes.resource(pipeline_name, resource_name)
        response = self._get(url)
        return PipelineResource(response, self)

    @ensure_annotations
    def resource_versions(self, pipeline_name: str, resource_name: str):
        url = self.routes.resource_versions(pipeline_name=pipeline_name, resource_name=resource_name)
        response = self._get(url)
        if not response:
            return [] # new resources can have no versions
        return [ResourceVersion(raw=raw, concourse_api=None) for raw in response]

    @ensure_annotations
    def list_workers(self):
        url = self.routes.list_workers()
        workers_list = self._get(url)
        return [Worker(raw=worker, concourse_api=None) for worker in workers_list]

    @ensure_annotations
    def prune_worker(self, worker_name: str):
        url = self.routes.prune_worker(worker_name)
        self._put(url, "")

    @ensure_annotations
    def abort_build(self, build_id):
        url = self.routes.abort_build(build_id)
        self._put(url, "")

    @ensure_annotations
    def pin_resource_version(self, pipeline_name: str, resource_name: str, resource_version_id: int):
        url = self.routes.pin_resource_version(pipeline_name, resource_name, resource_version_id)
        self._put(url, "")

    @ensure_annotations
    def pin_and_comment_resource_version(
        self, pipeline_name: str, resource_name: str, resource_version_id: int, comment: str
    ):
        self.pin_resource_version(pipeline_name, resource_name, resource_version_id)
        self.pin_comment(pipeline_name, resource_name, comment)

    @ensure_annotations
    def unpin_resource(self, pipeline_name: str, resource_name: str) -> bool:
        resource = self.resource(pipeline_name, resource_name)
        if resource.is_pinned():
            url = self.routes.unpin_resource(pipeline_name, resource_name)
            self._put(url, "")
            return True
        return False

    @ensure_annotations
    def pin_comment(self, pipeline_name: str, resource_name: str, comment: str):
        url = self.routes.pin_comment(pipeline_name, resource_name)
        pin_comment = json.dumps({'pin_comment': comment})
        self._put(url, pin_comment)

    def get_job(self, pipeline_name:str, job_name:str):
        url = self.routes.job(pipeline_name, job_name)
        return ConcourseJob(self._get(url))

    def set_team(
        self,
        team_name: str,
        username: str,
        role: str='member',
        github_auth_team: str=None,
    ):
        body = {
            "auth": {
                role: {
                    "users": [
                        "local:" + username,
                    ]
                }
            }
        }
        if github_auth_team:
            body["auth"][role].update({
                "groups": [
                    "github:" + github_auth_team,
                ]
            })

        team_url = self.routes.team_url(team_name)
        self._put(team_url, json.dumps(body))


class ConcourseApiV6_5_1(ConcourseApiBase):
    pass
