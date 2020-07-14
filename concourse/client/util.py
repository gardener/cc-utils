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

import dataclasses
import json
import time
import typing

import dateutil.parser

from dacite import from_dict
from dacite.exceptions import DaciteError
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import (
    datetime,
    timedelta,
)

from ci.util import (
    info,
    warning,
)
from whd.model import (
    PullRequestEvent,
)
from .model import (
    Job,
    PipelineConfigResource,
    PipelineResource,
    ResourceType,
    ResourceVersion,
    PinnedPRVersion,
    PinnedGitVersion,
    PinnedTimeVersion,
)


class PinningFailedError(Exception):
    pass


class PinningUnnecessaryError(Exception):
    pass


@dataclass
class PinComment:
    pin_timestamp: str
    version: typing.Union[PinnedPRVersion, PinnedGitVersion, PinnedTimeVersion]
    next_retry: str
    comment: str


def determine_pr_resource_versions(
    pr_event: PullRequestEvent,
    concourse_api,
) -> typing.Tuple[PipelineConfigResource, typing.Sequence[ResourceVersion]]:

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
        job for job in resource.pipeline.jobs() if job.is_triggered_by_resource(resource.name)
    )


def wait_for_job_to_be_triggered(
    job: Job,
    resource_version: ResourceVersion,
    concourse_api,
    retries: int=10,
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
) -> typing.Tuple[Job, PipelineConfigResource, ResourceVersion]:

    for pr_resource, pr_resource_versions in determine_pr_resource_versions(
        pr_event=pr_event,
        concourse_api=concourse_api,
    ):
        for job in determine_jobs_to_be_triggered(pr_resource):
            for pr_resource_version in pr_resource_versions:
                if not wait_for_job_to_be_triggered(job, pr_resource_version, concourse_api):
                    yield (job, pr_resource, pr_resource_version)


def pin_resource_and_trigger_build(
    job: Job,
    resource: PipelineConfigResource,
    resource_version: ResourceVersion,
    concourse_api,
    retries: int=3,
):
    # TODO: check whether resource is still pinned for our version
    with ensure_resource_unpinned(
        resource=resource,
        resource_version=resource_version,
        concourse_api=concourse_api,
    ):
        retries_for_job_trigger = 10
        sleep_seconds_between_attempts = 6
        wait_until_next_retry_seconds = retries_for_job_trigger * sleep_seconds_between_attempts

        # pin resource and add comment
        _pin_and_comment_resource(
            wait_until_next_retry_seconds=wait_until_next_retry_seconds,
            resource=resource,
            resource_version=resource_version,
            concourse_api=concourse_api,
        )

        for count in range(retries):
            concourse_api.trigger_build(
                pipeline_name=resource.pipeline_name(),
                job_name=job.name,
            )
            if wait_for_job_to_be_triggered(
                job=job,
                resource_version=resource_version,
                concourse_api=concourse_api,
                retries=retries_for_job_trigger,
                sleep_time_seconds=sleep_seconds_between_attempts,
            ):
                info(f'{job.name=} for {resource_version.version()=} has been triggered')
                return

            else:
                # job did not start, update pin comment with new retry timestamp
                _pin_and_comment_resource(
                    wait_until_next_retry_seconds=wait_until_next_retry_seconds,
                    resource=resource,
                    resource_version=resource_version,
                    concourse_api=concourse_api,
                )
                warning(
                    f'job {job.name} for resource version {resource_version.version()} '
                    f'could not be triggered. Retry {count + 1}/{retries}'
                )

        warning(
            f'job {job.name} for resource version {resource_version.version()} '
            f'could not be triggered. Giving up after {retries} retries'
        )


def _pin_and_comment_resource(
    wait_until_next_retry_seconds: int,
    resource: PipelineConfigResource,
    resource_version: ResourceVersion,
    concourse_api,
):
    now = datetime.now()
    next_retry = now + timedelta(seconds=wait_until_next_retry_seconds)
    pin_comment = PinComment(
        pin_timestamp=now.isoformat(),
        version=resource_version.version(),
        next_retry=next_retry.isoformat(),
        comment=(
            "pinned by technical user concourse, please do not unpin before "
            f"{next_retry.isoformat()}"
        ),
    )
    concourse_api.pin_and_comment_resource_version(
        pipeline_name=resource.pipeline_name(),
        resource_name=resource.name,
        resource_version_id=resource_version.id(),
        comment=json.dumps(dataclasses.asdict(pin_comment)),
    )


@contextmanager
def ensure_resource_unpinned(
    resource: PipelineResource,
    resource_version: ResourceVersion,
    concourse_api,
):
    try:
        _ensure_resource_unpinned(
            resource=resource,
            resource_version=resource_version,
            concourse_api=concourse_api,
        )
        yield
    finally:
        concourse_api.unpin_resource(
            pipeline_name=resource.pipeline_name(),
            resource_name=resource.name,
        )
        info(f'successfully unpinned {resource.name=}')


def _ensure_resource_unpinned(
    resource: PipelineResource,
    resource_version: ResourceVersion,
    concourse_api,
    maximal_checktime_seconds: int = 240,
):
    # maximal time to wait for pinned resource, after that we give up
    latest_unpin_checktime = datetime.now() + timedelta(seconds=maximal_checktime_seconds)

    while latest_unpin_checktime >= datetime.now():
        cc_resource = concourse_api.resource(
            pipeline_name=resource.pipeline_name(),
            resource_name=resource.name,
        )

        if not cc_resource.is_pinned():
            return

        sleep_time = 60
        try:
            pin_comment = from_dict(
                data_class=PinComment,
                data=json.loads(cc_resource.pin_comment()),
            )

            next_retry_time = dateutil.parser.isoparse(pin_comment.next_retry)
            sleep_time = min(
                (next_retry_time - datetime.now()).seconds + 5,
                maximal_checktime_seconds,
            )
            if next_retry_time <= datetime.now():
                warning(
                    'found resource which was not unpinned although timestamp expired: '
                    f'{resource.pipeline_name()=} {resource.name=}. Unpinning resource now'
                )
                concourse_api.unpin_resource(
                    pipeline_name=resource.pipeline_name(),
                    resource_name=resource.name,
                )
                return

        except json.JSONDecodeError:
            warning(
                f'Resource comment for {resource.name=} of {resource.pipeline_name()=} '
                'could not be parsed - most likely pinned by human user'
            )
        except DaciteError:
            warning(
                f'Resource comment for {resource.name=} of {resource.pipeline_name()=} '
                'could not be instantiated'
            )

        time.sleep(sleep_time)

    raise PinningFailedError(
        f'Tried at least {latest_unpin_checktime} seconds to unpin '
        f'{resource.name=} of pipeline {resource.pipeline_name()=}'
    )
