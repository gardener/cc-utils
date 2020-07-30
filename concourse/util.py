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
import functools
import json
import os

import concourse.client
import concourse.model.traits.meta
import concourse.steps.meta
import github.webhook

from concourse.client.routes import ConcourseApiRoutesBase
from model.concourse import (
    JobMappingSet,
)
from model.webhook_dispatcher import (
    WebhookDispatcherDeploymentConfig,
)
from ci.util import (
    _running_on_ci,
    check_env,
    create_url_from_attributes,
    ctx,
    info,
    warning,
)
import ccc.github


@dataclasses.dataclass
class PipelineMetaData:
    pipeline_name: str
    job_name: str
    current_config_set_name: str
    team_name: str


def sync_org_webhooks(whd_deployment_cfg: WebhookDispatcherDeploymentConfig,):
    '''Syncs required organization webhooks for a given webhook dispatcher instance'''

    for organization_name, github_api, webhook_url in \
            _enumerate_required_org_webhooks(whd_deployment_cfg=whd_deployment_cfg):

        webhook_syncer = github.webhook.GithubWebHookSyncer(github_api)
        failed_hooks = 0
        try:
            webhook_syncer.create_or_update_org_hook(
                organization_name=organization_name,
                webhook_url=webhook_url,
                skip_ssl_validation=False,
            )
            info(f'Created/updated organization hook for organization "{organization_name}"')
        except Exception as e:
            failed_hooks += 1
            warning(f'org: {organization_name} - error: {e}')

    if failed_hooks != 0:
        warning('Some webhooks could not be set - for more details see above.')


def _enumerate_required_org_webhooks(
    whd_deployment_cfg: WebhookDispatcherDeploymentConfig,
):
    '''Returns tuples of 'github orgname', 'github api object' and 'webhook url' '''
    cfg_factory = ctx().cfg_factory()

    whd_cfg_name = whd_deployment_cfg.webhook_dispatcher_config_name()
    whd_cfg = cfg_factory.webhook_dispatcher(whd_cfg_name)

    concourse_cfg_names = whd_cfg.concourse_config_names()
    concourse_cfgs = map(cfg_factory.concourse, concourse_cfg_names)

    for concourse_cfg in concourse_cfgs:
        job_mapping_set = cfg_factory.job_mapping(concourse_cfg.job_mapping_cfg_name())

        for github_orgname, github_cfg_name in _enumerate_github_org_configs(job_mapping_set):
            github_api = ccc.github.github_api(
                github_cfg=cfg_factory.github(github_cfg_name),
            )

            webhook_url = create_url_from_attributes(
                netloc=whd_deployment_cfg.external_url(),
                scheme='https',
                path='github-webhook',
                params='',
                query='{name}={value}'.format(
                    name=github.webhook.DEFAULT_ORG_HOOK_QUERY_KEY,
                    value=whd_cfg_name
                ),
                fragment=''
            )

            yield (github_orgname, github_api, webhook_url)


def _enumerate_github_org_configs(job_mapping_set: JobMappingSet,):
    '''Returns tuples of github org names and github config names'''
    for _, job_mapping in job_mapping_set.job_mappings().items():
        github_org_configs = job_mapping.github_organisations()

        for github_org_config in github_org_configs:
            yield (github_org_config.org_name(), github_org_config.github_cfg_name())


def resurrect_pods(
    namespace: str,
    concourse_client,
    kubernetes_client,
):
    '''
    concourse pods tend to crash and need to be pruned to help with the self-healing
    '''

    info('Checking for not running concourse workers')
    worker_list = concourse_client.list_workers()
    pruned_workers = []
    for worker in worker_list:
        worker_name = worker.name()
        info(f'Worker {worker_name}: {worker.state()}')
        if worker.state() != "running":
            warning(f'Prune worker {worker_name} and restart pod')
            pruned_workers.append(worker_name)
            concourse_client.prune_worker(worker_name)
            kubernetes_client.pod_helper().delete_pod(
                name=worker_name,
                namespace=namespace
            )
    return pruned_workers


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


def own_running_build_url(concourse_cfg):
    pipeline_metadata = get_pipeline_metadata()
    own_build = find_own_running_build()
    return ConcourseApiRoutesBase.running_build_url(
        concourse_cfg.external_url(),
        pipeline_metadata,
        own_build.build_number()
    )


@functools.lru_cache()
def find_own_running_build():
    '''
    Determines the current build job running on concourse by relying on the "meta" contract (
    see steps/meta), which prints a JSON document containing a UUID. By iterating through all
    current build jobs (considering running jobs only), and comparing the UUID read via file
    system and the UUID from build log output, it is possible to tell whether or not a given
    build job is the one from which this function was invoked.
    '''
    if not _running_on_ci():
        raise RuntimeError('Can only find own running build if running on CI infrastructure.')

    meta_dir = os.path.join(
        os.path.abspath(check_env('CC_ROOT_DIR')),
        concourse.model.traits.meta.DIR_NAME
    )
    meta_info_file = os.path.join(
        meta_dir,
        concourse.steps.meta.jobmetadata_filename,
    )

    with open(meta_info_file, 'r') as f:
        metadata_json = json.load(f)

    build_job_uuid = metadata_json['uuid']

    pipeline_metadata = get_pipeline_metadata()
    config_set = ctx().cfg_factory().cfg_set(pipeline_metadata.current_config_set_name)
    concourse_cfg = config_set.concourse()
    client = concourse.client.from_cfg(concourse_cfg, pipeline_metadata.team_name)

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

        uuid_line = None
        try:
            for line in reversed(list(build_events.iter_buildlog(meta_task_id))):
                if line.strip():
                    # last non empty line
                    uuid_line = line
                    break
        except StopIteration:
            pass

        uuid_json = json.loads(uuid_line)
        if uuid_json['uuid'] == build_job_uuid:
            return build
    else:
        raise RuntimeError('Could not determine own Concourse job.')
