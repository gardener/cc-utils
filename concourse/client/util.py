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

import typing

from whd.model import (
    PullRequestEvent,
)
from .model import (
    PipelineConfigResource,
    ResourceVersion,
)


def determine_pr_resource_versions(
    pr_event: PullRequestEvent,
    concourse_api
) -> typing.Iterator[(PipelineConfigResource, typing.Sequence[ResourceVersion])]:

    pr_resources = concourse_api.pipeline_resources(
        pipeline_names=concourse_api.pipelines(),
        resource_type='pull-request',
    )

    for pr_resource in pr_resources:
        ghs = pr_resource.github_source()
        pr_repository = pr_event.repository()
        if not ghs.hostname() == pr_repository.github_host():
            continue
        if not ghs.repo_path().lstrip('/') == pr_repository.repository_path():
            continue

        pipeline_name = pr_resource.pipeline.name
        pr_resource_versions = concourse_api.resource_versions(
            pipeline_name=pipeline_name,
            resource_name=pr_resource.name,
        )

        # only interested in pr resource versions, which match the PR number
        pr_resource_versions = [
            rv for rv in pr_resource_versions if rv.version()['pr'] == str(pr_event.number())
        ]

        yield (pr_resource, pr_resource_versions)
