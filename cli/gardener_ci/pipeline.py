import dataclasses
import logging
import os
import sys
import urllib.parse

import git
import yaml

import gci.componentmodel as cm

import ci.util
import concourse.enumerator as ce

logger = logging.getLogger('pipeline-cli')


def _branch_cfg(
    repo: git.Repo(),
    meta_ci_ref: str,
    absent_ok: bool,
) -> ce.BranchCfg | None:
    '''
    parses `branch.cfg` from repo, assuming it is a valid yaml-file named `branch.cfg`
    '''
    for ref in repo.refs:
        if ref.path == meta_ci_ref:
            break
    else:
        if absent_ok:
            return None
        raise RuntimError(f'{meta_ci_ref=} not available in local {repo=}')

    for blob in ref.object.tree.blobs:
        if blob.name == 'branch.cfg':
            break
    else:
        raise RuntimError(f'did not find regular file `branch.cfg` in {meta_ci_ref}')

    branch_cfg = yaml.safe_load(blob.data_stream)
    return ce.BranchCfg(raw_dict=branch_cfg)


def _pipeline_definitions(
    repo: git.Repo,
    branch_name: str,
    branch_cfg: ce.BranchCfg | None=None,
):
    '''
    parses pipeline-definitions from .ci/pipeline_definitions from current repo's work-tree
    '''
    with open(os.path.join(repo.working_tree_dir, '.ci', 'pipeline_definitions')) as f:
        raw = yaml.safe_load(f)

    if not branch_cfg:
        return raw

    cfg_entry = branch_cfg.cfg_entry_for_branch(branch=branch_name)

    return ci.util.merge_dicts(
        raw,
        cfg_entry.override_definitions(),
    )


def _component_name(
    repo: git.Repo,
    job: dict,
):
    try:
        component_descriptor_trait = job['traits']['component_descriptor']
        if name := component_descriptor_trait['component_name']:
            return name
    except (KeyError, TypeError):
        pass

    remote = repo.remote()
    logger.info(f'{remote.name=}')

    url = urllib.parse.urlparse(next(remote.urls))
    component_name = f'{url.hostname}{url.path}'
    return component_name


def _main_source(
    repo: git.Repo,
    version: str,
    job: dict,
):
    return cm.ComponentSource(
        name='dummy-name',
        version=version,
        access=cm.GithubAccess(
            type=cm.AccessType.GITHUB,
            repoUrl=next(repo.remote().urls),
            ref=repo.active_branch.name,
        ),
    ),


def _iter_resources(
    version: str,
    job: dict,
):
    if not (traits := job.get('traits', None)):
        return

    if not (publish_trait := traits.get('publish', None)):
        return

    if not (images := publish_trait.get('dockerimages', None)):
        return

    for name, image in images.items():
        yield cm.Resource(
            name=name,
            version=version,
            type=cm.ArtefactType.OCI_IMAGE,
            access=cm.OciAccess(
                type=cm.AccessType.OCI_REGISTRY,
                imageReference=image['image'] + ':' + version,
            ),
            labels=image.get('resource_labels', []),
        )


def base_component_descriptor(
    repo: str=None,
    meta_ci: str='if-local',
    meta_ci_ref: str='refs/meta/ci',
    pipeline_name: str=None, # only required if there is more than one
    job_name: str=None,
    component_name: str=None,
    version: str=None,
    outfile: str=None,
):
    if not repo:
        repo = os.getcwd()

    repo = git.Repo(
        path=repo,
        search_parent_directories=True,
    )

    try:
        branch_name = repo.active_branch.name
    except TypeError:
        logger.error('repository must not be in detched-head state')
        exit(1)

    logger.info(f'using {branch_name=}')
    logger.info('looking for branch-cfg (refs/meta/ci)')
    branch_cfg = _branch_cfg(
        repo=repo,
        meta_ci_ref=meta_ci_ref,
        absent_ok=meta_ci == 'if-local',
    )
    if not branch_cfg:
        logger.info('did not find branch-cfg (ignoring)')

    pipeline_definitions = _pipeline_definitions(
        repo=repo,
        branch_name=branch_name,
        branch_cfg=branch_cfg,
    )

    if not pipeline_definitions:
        logger.error('pipeline-definitions appear to be empty')
        exit(1)

    if not pipeline_name and len(pipeline_definitions) > 1:
        logger.error('more than one pipeline found - must specify name')
        logger.info('pipeline-names:')
        for name in pipeline_definitions:
            print(name)
        exit(1)

    if not pipeline_name:
        pipeline_name, pipeline_definition = next(pipeline_definitions.items().__iter__())
    else:
        pipeline_name, pipeline_definition = pipeline_definitions[pipeline_name]

    logger.info(f'{pipeline_name=}')

    jobs = pipeline_definition['jobs']
    base_definition = pipeline_definition.get('base_definition', {})

    for name, job in jobs.items():
        jobs[name] = ci.util.merge_dicts(job, base_definition)

    if not job_name:
        logger.info('guessing best job-name, as none was specified')
        best_score = 0
        best_candidate = None
        best_job_name = None

        for job_name, job in jobs.items():
            traits = job.get('traits', {})
            score = 0
            if not 'component_descriptor' in traits:
                continue # not a candidate

            # jobs w/ release-trait publish "final" component-descriptors, thus they should
            # be strongly preferred over jobs that do not have release-trait
            if 'release' in traits:
                score += 2

            # publish-trait contribute to base-component-descriptor
            if 'publish' in traits:
                score += 1

            if score <= best_score:
                continue # candidate was not better

            best_candidate = job
            best_score = score
            best_job_name = job_name

        logger.info('guessed job-name (pass explicitly if guessed wrong)')
        job = best_candidate
        job_name = best_job_name
    else:
        job = jobs[job_name]

    logger.info(f'{job_name=}')

    if not component_name:
        component_name = _component_name(
            repo=repo,
            job=job,
        )
        logger.info(f'guessed {component_name=} (from default remote-url)')

    if not version:
        version = '1.2.3'

    component_descriptor = cm.ComponentDescriptor(
        meta=cm.Metadata(),
        component=cm.Component(
            name=component_name,
            version=version,
            repositoryContexts=[],
            provider='sap-se',
            componentReferences=[],
            resources=[
                r for r in _iter_resources(
                    version=version,
                    job=job,
                )
            ],
            sources=[
                _main_source(
                    repo=repo,
                    job=job,
                    version=version,
                ),
            ],
        ),
    )

    if outfile:
        outfileh = open(outfile, 'w')
    else:
        outfileh = sys.stdout

    yaml.dump(
        data=dataclasses.asdict(component_descriptor),
        stream=outfileh,
        Dumper=cm.EnumValueYamlDumper,
    )
