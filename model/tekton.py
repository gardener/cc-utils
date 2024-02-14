# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


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
