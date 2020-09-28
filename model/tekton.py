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

from model.base import (
    ModelBase,
    NamedModelElement,
)

import github.util

from github3 import GitHub

TEKTON_GITHUB_ORG = 'tektoncd'
TEKTON_PIPELINES_REPO_NAME = 'pipeline'
TEKTON_DASHBOARD_REPO_NAME = 'dashboard'

TEKTON_PIPELINES_RELEASE_ASSET_NAME = 'release.yaml'
TEKTON_DASHBOARD_RELEASE_ASSET_NAME = 'tekton-dashboard-release.yaml'


class TektonConfig(NamedModelElement):
    '''Not intended to be instantiated by users of this module
    '''
    def pipelines_config(self):
        if raw_cfg := self.raw.get('pipelines'):
            return TektonPipelinesConfig(raw_cfg)
        return None

    def dashboard_config(self):
        if raw_cfg := self.raw.get('dashboard'):
            return TektonDashboardConfig(raw_cfg)
        return None

    def kubernetes_config_name(self):
        return self.raw['kubernetes_config']

    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from [
            'kubernetes_config',
        ]

    def _optional_attributes(self):
        yield from super()._optional_attributes()
        yield from [
            'dashboard',
            'pipelines',
        ]


class TektonPipelinesConfig(ModelBase):
    def namespace(self):
        return self.raw.get('namespace')

    def version(self):
        return self.raw.get('version')

    def install_manifests(self):
        gh_helper = github.util.GitHubRepositoryHelper(
            owner=TEKTON_GITHUB_ORG,
            name=TEKTON_PIPELINES_REPO_NAME,
            github_api=GitHub(),
        )
        return gh_helper.retrieve_asset_contents(
            release_tag=self.version(),
            asset_label=TEKTON_PIPELINES_RELEASE_ASSET_NAME,
        )

    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from [
            'namespace'
            'version',
        ]


class TektonDashboardConfig(ModelBase):
    def namespace(self):
        return self.raw.get('namespace')

    def version(self):
        return self.raw.get('version')

    def install_manifests(self):
        gh_helper = github.util.GitHubRepositoryHelper(
            owner=TEKTON_GITHUB_ORG,
            name=TEKTON_DASHBOARD_REPO_NAME,
            github_api=GitHub(),
        )
        return gh_helper.retrieve_asset_contents(
            release_tag=self.version(),
            asset_label=TEKTON_DASHBOARD_RELEASE_ASSET_NAME,
        )

    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from [
            'namespace'
            'version',
        ]
