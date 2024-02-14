# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import dataclasses
import functools
import json
import logging
import os

import concourse.client.model
import concourse.model.traits.meta
import concourse.steps.meta

from concourse.client.routes import ConcourseApiRoutesBase
from ci.util import (
    _running_on_ci,
    check_env,
    ctx,
)
import ccc.concourse
import ccc.github
import ci.log

logger = logging.getLogger()
ci.log.configure_default_logging()


@dataclasses.dataclass
class PipelineMetaData:
    pipeline_name: str
    job_name: str
    current_config_set_name: str
    team_name: str


def get_pipeline_metadata():
    if not _running_on_ci():
        raise RuntimeError('Pipeline-metadata is only available if running on CI infrastructure')

    current_cfg_set_name = check_env('CONCOURSE_CURRENT_CFG')
    team_name = check_env('CONCOURSE_CURRENT_TEAM')
    pipeline_name = check_env('PIPELINE_NAME')
    job_name = check_env('BUILD_JOB_NAME')

    return PipelineMetaData(
        pipeline_name=pipeline_name,
        job_name=job_name,
        current_config_set_name=current_cfg_set_name,
        team_name=team_name,
    )


def _current_concourse_config(cfg_factory=None):
    if not _running_on_ci():
        raise RuntimeError('Can only determine own concourse config if running on CI')

    if cfg_factory:
        cfg_set = cfg_factory.cfg_set(check_env('CONCOURSE_CURRENT_CFG'))
        concourse_cfg = cfg_set.concourse()
    else:
        concourse_cfg = ctx().cfg_set().concourse()

    return concourse_cfg


def own_running_build_url(cfg_factory=None) -> str:
    if not _running_on_ci():
        raise RuntimeError('Can only determine own build url if running on CI infrastructure')

    pipeline_metadata = get_pipeline_metadata()

    own_build = find_own_running_build(cfg_factory=cfg_factory)
    cc_cfg = _current_concourse_config(cfg_factory=cfg_factory)

    return ConcourseApiRoutesBase.running_build_url(
        cc_cfg.external_url(),
        pipeline_metadata,
        own_build.build_number(),
    )


def meta_info_file_from_env() -> str:
    return os.path.abspath(
        os.path.join(
            check_env('CC_ROOT_DIR'),
            concourse.model.traits.meta.DIR_NAME,
            concourse.steps.meta.jobmetadata_filename,
        )
    )


def has_metadata() -> bool:
    return os.path.isfile(
        meta_info_file_from_env()
    )


@functools.lru_cache()
def find_own_running_build(cfg_factory=None) -> concourse.client.model.Build:
    '''
    Determines the current build job running on concourse by relying on the "meta" contract (
    see steps/meta), which prints a JSON document containing a UUID. By iterating through all
    current build jobs (considering running jobs only), and comparing the UUID read via file
    system and the UUID from build log output, it is possible to tell whether or not a given
    build job is the one from which this function was invoked.
    '''
    if not _running_on_ci():
        raise RuntimeError('Can only find own running build if running on CI infrastructure.')

    meta_info_file = meta_info_file_from_env()

    with open(meta_info_file, 'r') as f:
        metadata_json = json.load(f)

    build_job_uuid = metadata_json['uuid']

    if cfg_factory:
        current_cfg_set_name = check_env('CONCOURSE_CURRENT_CFG')
        cfg_set = cfg_factory.cfg_set(current_cfg_set_name)
    else:
        cfg_set = None

    pipeline_metadata = get_pipeline_metadata()
    client = ccc.concourse.client_from_env(
        team_name=pipeline_metadata.team_name,
        cfg_set=cfg_set,
    )

    # only consider limited amount of jobs to avoid large number of requests in case we do not
    # find ourself (assumption: there are only few running jobs in parallel at a given time)
    consider_builds = 20
    builds = client.job_builds(pipeline_metadata.pipeline_name, pipeline_metadata.job_name)
    builds = [
        build for build in builds
        if build.status() is concourse.client.model.BuildStatus.RUNNING
    ][:consider_builds]

    # avoid parsing too much output. usually, there will be only one line (our JSON output)
    # sometimes (new image version is retrieved), there will be a few lines more.
    for build in builds:
        build_events = build.events()
        build_plan = build.plan()
        meta_task_id = build_plan.task_id(concourse.model.traits.meta.META_STEP_NAME)

        # we expect output to only contain valid JSON
        meta_output = ''.join(build_events.iter_buildlog(meta_task_id)).strip()

        try:
            uuid_json = json.loads(meta_output)
        except json.decoder.JSONDecodeError:
            logger.error(f'Error when parsing {meta_output=}')
            continue # ignore - we might still find "our" job
        if uuid_json['uuid'] == build_job_uuid:
            return build
    else:
        raise RuntimeError('Could not determine own Concourse job.')
