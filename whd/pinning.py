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

import logging
import typing
from dataclasses import dataclass

from .model import(
    PullRequestEvent,
)
from concourse.client.model import (
    ResourceVersion,
    Job,
    Resource,
)


logger = logging.getLogger(__name__)


@dataclass
class JobResourceVersion:
    job: Job
    resource: Resource
    resource_version: ResourceVersion


def jobs_not_triggered(
    self,
    concourse_api,
    resources: typing.Sequence[Resource],
    pr_event: PullRequestEvent,
) -> JobResourceVersion:
    '''
    @return all jobs and the corresponding resource versions, which have not been triggered
    '''
    for resource in resources:
        pipeline_name = resource.pipeline.name
        resource_versions = concourse_api.resource_versions(
            pipeline_name=pipeline_name,
            resource_name=resource.name,
            pr_number=pr_event.number(),
        )
        pr_resource_versions = [
            rv for rv in resource_versions if rv.version()['pr'] == str(pr_event.number())
        ]
        if not pr_resource_versions:
            logger.warning(
                f"no resource versions found for pipeline {pipeline_name} "
                f"resource {resource.name} and PR {pr_event.number()}"
            )
            return

        # get all jobs which should be triggered by the resource
        jobs_which_should_be_triggered = [
            job for job in resource.pipeline.jobs if job.is_triggered_by_resource(resource.name)
        ]

        # check if there is a pr resource version which has not triggered a job.
        # If so return the job and the corresponding resource version
        for job in jobs_which_should_be_triggered:
            builds = concourse_api.job_builds(pipeline_name, job.name)
            for resource_version in resource_versions_without_build(
                resource_versions=pr_resource_versions,
                builds=builds,
            ):
                yield JobResourceVersion(
                    job=job,
                    resource=resource,
                    resource_version=resource_version
                )


def resource_versions_without_build(
    self,
    resource_versions: typing.List[ResourceVersion],
    builds: typing.List[Job],
):
    '''
    yield resource versions for which no build exists by checking if the resource version
    reference is found in any existing build
    '''
    for resource_version in resource_versions:
        resource_version_in_build = False
        for build in reversed(builds[-30:]):
            build_plan = build.plan()
            if build_plan.contains_version_ref(resource_version.version()['ref']):
                resource_version_in_build = True
                break
        if not resource_version_in_build:
            yield resource_version
