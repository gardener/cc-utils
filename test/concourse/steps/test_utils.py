# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import os

import concourse.model.base
import concourse.model.step
import concourse.model.job
import concourse.model.resources

# helper functions to create dummy pipeline definition objects


def pipeline_step(name):
    return concourse.model.step.PipelineStep(
        name=name,
        is_synthetic=True,
        script_type=concourse.model.base.ScriptType.PYTHON3,
        raw_dict={}
    )


def repository():
    return concourse.model.resources.RepositoryConfig(
        logical_name='main',
        raw_dict={
            'branch': 'master_branch',
            'path': 'org/repo_name',
        }
    )


def resource_registry():
    registry = concourse.model.resources.ResourceRegistry()
    # add dummy "main repository"
    return registry


def job(main_repo):
    job_variant = concourse.model.job.JobVariant(
        name='dummy_job',
        resource_registry=resource_registry(),
        raw_dict={},
    )

    job_variant._repos_dict = {main_repo.logical_name(): main_repo}
    job_variant._main_repository_name = main_repo.logical_name()
    job_variant._steps_dict = {}

    return job_variant


def populate_meta_dir(directory:str):
    for n in (
        'build-id',
        'build-name',
        'build-job-name',
        'build-team-name',
        'build-pipeline-name',
        'atc-external-url',
    ):
        with open(os.path.join(directory, n), 'w') as f:
            f.write(n)
