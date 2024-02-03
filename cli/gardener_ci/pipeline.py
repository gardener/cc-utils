import dataclasses
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse

import git
import yaml

import gci.componentmodel as cm

import ci.util
import concourse.enumerator as ce

logger = logging.getLogger('pipeline-cli')

own_dir = os.path.abspath(os.path.dirname(__file__))


def _branch_cfg(
    repo: git.Repo,
    meta_ci_ref: str,
    fetch: bool=False,
    absent_ok: bool=True,
) -> ce.BranchCfg | None:
    '''
    parses `branch.cfg` from repo, assuming it is a valid yaml-file named `branch.cfg`
    '''
    if fetch:
        remote = repo.remote()
        remote.fetch(f'refs/meta/ci:{meta_ci_ref}')

    for ref in repo.refs:
        if ref.path == meta_ci_ref:
            break
    else:
        if absent_ok:
            return None
        raise RuntimeError(f'{meta_ci_ref=} not available in local {repo=}')

    for blob in ref.object.tree.blobs:
        if blob.name == 'branch.cfg':
            break
    else:
        raise RuntimeError(f'did not find regular file `branch.cfg` in {meta_ci_ref}')

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

    if not cfg_entry:
        return raw

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
    hostname = url.hostname.removeprefix('gardener.')
    component_name = f'{hostname}{url.path}'
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
            commit=repo.head.commit.hexsha,
        ),
        labels=[
            cm.Label(
                name='cloud.gardener/cicd/source',
                value={
                    'repository-classification': 'main',
                },
            ),
        ],
    )


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
        if 'image' in image:
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
        else:
            for target in image['targets']:
                yield cm.Resource(
                    name=target['name'],
                    version=version,
                    type=cm.ArtefactType.OCI_IMAGE,
                    access=cm.OciAccess(
                        type=cm.AccessType.OCI_REGISTRY,
                        imageReference=target['image'] + ':' + version,
                    ),
                    labels=image.get('resource_labels', []),
                )


def _repo(repo: str=None):
    if isinstance(repo, git.Repo):
        return repo

    if not repo:
        repo = os.getcwd()

    try:
        repo = git.Repo(
            path=repo,
            search_parent_directories=True,
        )
        return repo
    except git.exc.InvalidGitRepositoryError: # pylint: disable=E1101
        logger.error(f'not a git-repository: {repo}. Hint: change PWD, or pass --repo')
        exit(1)


def base_component_descriptor(
    repo: str=None,
    meta_ci: str='if-local', # | fetch
    meta_ci_ref: str='refs/meta/ci',
    pipeline_name: str=None, # only required if there is more than one
    job_name: str=None,
    component_name: str=None,
    ocm_repo: str='europe-docker.pkg.dev/gardener-project/public',
    version: str=None,
    outfile: str=None,
):
    repo = _repo(repo=repo)

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
        fetch=meta_ci == 'fetch',
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
        logger.info('more than one pipeline found - will guess')

    if not pipeline_name:
        pipeline_name, pipeline_definition = next(pipeline_definitions.items().__iter__())
    else:
        pipeline_definition = pipeline_definitions[pipeline_name]

    logger.info(f'{pipeline_name=}')

    best_score = 0
    best_candidate = None
    best_job_name = None

    if not job_name:
        logger.info('guessing best job-name, as none was specified')

    for p_name, pipeline_definition in pipeline_definitions.items():
        if pipeline_name and pipeline_name != p_name:
            continue

        if not (jobs := pipeline_definition.get('jobs', None)):
            logger.error(f'local {pipeline_name=} contained no jobs')
            logger.info('hint:fetch refs/meta/ci (--meta-ci=fetch)')
            exit(1)

        base_definition = pipeline_definition.get('base_definition', {})

        for j_name, job in jobs.items():
            jobs[j_name] = ci.util.merge_dicts(base_definition, job)

        for j_name, job in jobs.items():
            if job_name and job_name != j_name:
                continue

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
            best_job_name = j_name

            logger.info('guessed job-name (pass explicitly if guessed wrong)')
            job = best_candidate

    job_name = best_job_name
    job = best_candidate

    if not job_name:
        logger.error('did not find any job w/ at least component_descriptor trait')
        logger.info('hint: fetch refs/meta/ci (--meta-ci=fetch)')
        exit(1)

    logger.info(f'{job_name=}')

    if not component_name:
        component_name = _component_name(
            repo=repo,
            job=job,
        )
        logger.info(f'guessed {component_name=} (from default remote-url)')

    if not version:
        if os.path.isfile(versionfile := os.path.join(repo.working_tree_dir, 'VERSION')):
            version = open(versionfile).read().strip()
            if version.startswith('v'):
                prefix = 'v'
            else:
                prefix = ''
            import version as version_mod
            version = version_mod.parse_to_semver(version)
            version = str(version.finalize_version())
        else:
            version = f'{prefix}1.2.3'

    component_descriptor = cm.ComponentDescriptor(
        meta=cm.Metadata(),
        component=cm.Component(
            name=component_name,
            version=version,
            repositoryContexts=[
                cm.OciOcmRepository(
                    type=cm.AccessType.OCI_REGISTRY,
                    baseUrl=ocm_repo,
                    subPath='',
                ),
            ],
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

    return component_descriptor


def component_descriptor(
    repo: str=None,
    meta_ci: str='if-local', # | fetch
    meta_ci_ref: str='refs/meta/ci',
    pipeline_name: str=None, # only required if there is more than one
    job_name: str=None,
    component_name: str=None,
    version: str=None,
    outfile: str=None,
    component_repo: str='europe-docker.pkg.dev/gardener-project/public',
    component_descriptor_script: str=None,
    base_component_descriptor_path: str=None,
):
    repo = _repo(repo=repo)
    if not outfile:
        outfile = os.path.join(os.getcwd(), 'component-descriptor.yaml')

    if not component_descriptor_script:
        component_descriptor_script = os.path.join(
            repo.working_tree_dir,
            '.ci',
            'component_descriptor',
        )

    base_component_descriptor_file = tempfile.NamedTemporaryFile()
    logger.info(f'{base_component_descriptor_file.name=}')
    logger.info(f'{outfile=}')

    if not base_component_descriptor_path:
        base_descriptor  = base_component_descriptor(
            repo=repo,
            meta_ci=meta_ci,
            meta_ci_ref=meta_ci_ref,
            pipeline_name=pipeline_name,
            job_name=job_name,
            component_name=component_name,
            version=version,
            outfile=base_component_descriptor_file.name,
        )
    else:
        shutil.copyfile(base_component_descriptor_path, base_component_descriptor_file.name)
        with open(base_component_descriptor_path) as f:
            raw = yaml.safe_load(f)
        base_descriptor = cm.ComponentDescriptor.from_dict(raw)

    base_component = base_descriptor.component

    if not os.path.isfile(component_descriptor_script):
        logger.info(f'{component_descriptor_script=} is not a file - skipping callback')

        shutil.copyfile(
            base_component_descriptor_file.name,
            outfile,
        )
        logger.info(f'copied to {outfile=}')
        exit(0)

    cli = os.path.join(own_dir, 'cli_gen.py')
    dependencies_cmd = ' '.join((
        cli,
        'productutil_v2',
        'add_dependencies',
        '--descriptor-src-file', base_component_descriptor_file.name,
        '--descriptor-out-file', base_component_descriptor_file.name,
        '--component-version', base_component.version,
        '--component-name', base_component.name,
    ))

    env = os.environ.copy()
    env |= {
        'MAIN_REPO_DIR': repo.working_tree_dir,
        'BASE_DEFINITION_PATH': base_component_descriptor_file.name,
        'COMPONENT_DESCRIPTOR_PATH': outfile,
        'COMPONENT_NAME': base_component.name,
        'COMPONENT_VERSION': base_component.version,
        'EFFECTIVE_VERSION': base_component.version,
        'CURRENT_COMPONENT_REPOSITORY': component_repo,
        'ADD_DEPENDENCIES_CMD': dependencies_cmd,
    }

    subprocess.run(
        (component_descriptor_script,),
        env=env,
        check=True,
    )

    logger.info(f'component-descriptor should be at: {outfile=}')
