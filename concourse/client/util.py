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

import time
import typing

from whd.model import (
    PullRequestEvent,
)
from .model import (
    Job,
    PipelineConfigResource,
    ResourceType,
    ResourceVersion,
)


def determine_pr_resource_versions(
    pr_event: PullRequestEvent,
    concourse_api
) -> typing.Iterator[(PipelineConfigResource, typing.Sequence[ResourceVersion])]:

    pr_resources = concourse_api.pipeline_resources(
        pipeline_names=concourse_api.pipelines(),
        resource_type=ResourceType.PULL_REQUEST,
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


def determine_jobs_to_be_triggered(
    resource: PipelineConfigResource,
) -> typing.Iterator[Job]:

    yield from (
        job for job in resource.pipeline.jobs if job.is_triggered_by_resource(resource.name)
    )


def wait_for_job_to_be_triggered(
    job: Job,
    resource_version: ResourceVersion,
    concourse_api,
    retries: int=8,
    sleep_time_seconds: int=5,
) -> bool:
    '''
    There is some delay between the update of a resource and the start of the corresponding job.
    Therefore we have to wait some time to decide if the job has been triggered or not.

    @return True if the job has been triggered
    '''
    while retries >= 0:
        if has_job_been_triggered(
            job=job,
            resource_version=resource_version,
            concourse_api=concourse_api,
        ):
            return True

        time.sleep(sleep_time_seconds)
        retries -= 1

    return False


def has_job_been_triggered(
    job: Job,
    resource_version: ResourceVersion,
    concourse_api,
) -> bool:

    builds = concourse_api.job_builds(job.pipeline.name, job.name)
    for build in builds:
        build_plan = build.plan()
        if build_plan.contains_version_ref(resource_version.version()['ref']):
            return True
    return False


def jobs_not_triggered(
    pr_event: PullRequestEvent,
    concourse_api,
) -> typing.Iterator[(Job, PipelineConfigResource, ResourceVersion)]:

    for pr_resource, pr_resource_versions in determine_pr_resource_versions(
        pr_event=pr_event,
        concourse_api=concourse_api,
    ):
        for job in determine_jobs_to_be_triggered(pr_resource):
            for pr_resource_version in pr_resource_versions:
                if not wait_for_job_to_be_triggered(job, pr_resource_version, concourse_api):
                    yield (job, pr_resource, pr_resource_version)
