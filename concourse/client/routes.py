# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


from ensure import ensure_annotations
from urllib.parse import urljoin

import ci.util

CONCOURSE_API_SUFFIX = 'api/v1'


class ConcourseApiRoutesBase:
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
        base_url += '/'

        return urljoin(base_url, '/'.join(parts))

    @staticmethod
    def running_build_url(concourse_base_url, pipeline_metadata, build_number):
        url_parts = [
            concourse_base_url,
            'teams',
            pipeline_metadata.team_name,
            'pipelines',
            pipeline_metadata.pipeline_name,
            'jobs',
            pipeline_metadata.job_name,
            'builds',
            build_number,
        ]
        return ci.util.urljoin(*url_parts)

    @ensure_annotations
    def team_url(self, team: str=None):
        if not team:
            team = self.team
        return self._api_url('teams', team, prefix_team=False)

    def login(self):
        return ci.util.urljoin(
            self.base_url,
            'sky',
            'token'
        )

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
    def pause_pipeline(self, pipeline_name: str):
        return self._api_url('pipelines', pipeline_name, 'pause')

    @ensure_annotations
    def expose_pipeline(self, pipeline_name: str):
        return self._api_url('pipelines', pipeline_name, 'expose')

    @ensure_annotations
    def resource_check(self, pipeline_name: str, resource_name: str):
        return self._api_url('pipelines', pipeline_name, 'resources', resource_name, 'check')

    @ensure_annotations
    def resource(self, pipeline_name: str, resource_name: str):
        return self._api_url('pipelines', pipeline_name, 'resources', resource_name)

    @ensure_annotations
    def resource_versions(self, pipeline_name: str, resource_name: str):
        return self._api_url('pipelines', pipeline_name, 'resources', resource_name, 'versions')

    @ensure_annotations
    def job_builds(self, pipeline_name: str, job_name: str):
        return self._api_url('pipelines', pipeline_name, 'jobs', job_name, 'builds')

    @ensure_annotations
    def job_build(self, pipeline_name: str, job_name: str, build_name: str):
        return self._api_url('pipelines', pipeline_name, 'jobs', job_name, 'builds', build_name)

    @ensure_annotations
    def job(self, pipeline_name: str, job_name: str):
        return self._api_url('pipelines', pipeline_name, 'jobs', job_name)

    @ensure_annotations
    def pause_job(self, pipeline_name: str, job_name: str):
        return self._api_url('pipelines', pipeline_name, 'jobs', job_name, 'pause')

    @ensure_annotations
    def unpause_job(self, pipeline_name: str, job_name: str):
        return self._api_url('pipelines', pipeline_name, 'jobs', job_name, 'unpause')

    @ensure_annotations
    def build_events(self, build_id):
        return self._api_url('builds', str(build_id), 'events', prefix_team=False)

    @ensure_annotations
    def build_plan(self, build_id):
        return self._api_url('builds', str(build_id), 'plan', prefix_team=False)

    @ensure_annotations
    def abort_build(self, build_id):
        return self._api_url('builds', str(build_id), 'abort', prefix_team=False)

    @ensure_annotations
    def list_workers(self):
        return self._api_url('workers', prefix_team=False)

    @ensure_annotations
    def prune_worker(self, worker_name: str):
        return self._api_url('workers', worker_name, 'prune', prefix_team=False)

    @ensure_annotations
    def pin_resource_version(self, pipeline_name: str, resource_name: str, resource_version_id: int):
        return self._api_url(
            'pipelines', pipeline_name,
            'resources', resource_name,
            'versions', str(resource_version_id),
            'pin'
        )

    @ensure_annotations
    def unpin_resource(self, pipeline_name: str, resource_name: str):
        return self._api_url('pipelines', pipeline_name, 'resources', resource_name, 'unpin')

    @ensure_annotations
    def pin_comment(self, pipeline_name: str, resource_name: str):
        return self._api_url('pipelines', pipeline_name, 'resources', resource_name, 'pin_comment')


class ConcourseApiRoutesV6_3_0(ConcourseApiRoutesBase):
    '''Routes for Concourse v6.3.0'''

    def login(self):
        return ci.util.urljoin(
            self.base_url,
            'sky',
            'issuer',
            'token',
        )
