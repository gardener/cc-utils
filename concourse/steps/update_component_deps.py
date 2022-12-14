import functools
import logging
import os
import subprocess
import tempfile
import traceback
import typing

import gci.componentmodel
import github3.exceptions
import github3.repos.repo

import ccc.github
import ci.util
import cnudie.util
import cnudie.retrieve
import concourse.model.traits.update_component_deps
import concourse.steps.component_descriptor_util as cdu
import concourse.paths
import dockerutil
import github.util
import gitutil
import model.container_registry as cr
import product.v2
import version
from concourse.model.traits.update_component_deps import (
    MergePolicy,
    MergeMethod,
)
from github.util import (
    GitHubRepoBranch,
)
from github.release_notes.util import ReleaseNotes

logger = logging.getLogger('step.update_component_deps')


UpstreamUpdatePolicy = concourse.model.traits.update_component_deps.UpstreamUpdatePolicy


@functools.cache
def component_descriptor_lookup() -> cnudie.retrieve.ComponentDescriptorLookupById:
    return cnudie.retrieve.create_default_component_descriptor_lookup()


def current_product_descriptor():
    component_descriptor_file_path = cdu.component_descriptor_path(
        schema_version=gci.componentmodel.SchemaVersion.V2,
    )
    ctf_out_file_path = os.path.join(
      ci.util.check_env('COMPONENT_DESCRIPTOR_DIR'),
      product.v2.CTF_OUT_DIR_NAME,
    )

    # cd is supplied via component-descriptor file. Parse and return
    if os.path.isfile(component_descriptor_file_path):
        return gci.componentmodel.ComponentDescriptor.from_dict(
            component_descriptor_dict=ci.util.parse_yaml_file(component_descriptor_file_path,)
        )

    # cd is supplied via CTF archive. Parse ctf archive and return correct one
    elif os.path.isfile(ctf_out_file_path):
        component_name = ci.util.check_env('COMPONENT_NAME')
        component_descriptors = [
            cd
            for cd in cnudie.util.component_descriptors_from_ctf_archive(ctf_out_file_path)
            if cd.component.name == component_name
        ]
        if (cds_len := len(component_descriptors)) == 0:
            raise RuntimeError(
                f"No component descriptor for component '{component_name}' found in ctf archive "
                f"at '{ctf_out_file_path}'"
            )
        elif cds_len > 1:
            raise RuntimeError(
                f"More than one component descriptor for component '{component_name}' found in ctf "
                f"archive at '{ctf_out_file_path}'"
            )
        else:
            return component_descriptors[0]

    # either ctf or cd-file _must_ exist (enforced by component-descriptor-trait)
    else:
        raise RuntimeError(
            f'Neither component-descriptor file at {component_descriptor_file_path=} or '
            f'ctf-archive at {ctf_out_file_path=} exist'
        )


def current_component():
    return current_product_descriptor().component


def close_obsolete_pull_requests(
    upgrade_pull_requests,
    reference_component: gci.componentmodel.Component,
):
    obsolete_upgrade_requests = [
        pr for pr in upgrade_pull_requests
        if pr.pull_request.state == 'open' and pr.is_obsolete(
            reference_component=reference_component,
        )
    ]

    for obsolete_request in obsolete_upgrade_requests:
        obsolete_request.purge()


def upgrade_pr_exists(
    component_reference: gci.componentmodel.ComponentReference,
    component_version: str,
    upgrade_requests: typing.Iterable[github.util.UpgradePullRequest],
) -> github.util.UpgradePullRequest | None:
    if any(
        (matching_rq := upgrade_rq).target_matches(
            reference=component_reference,
            reference_version=component_version,
        )
        for upgrade_rq in upgrade_requests
    ):
        return matching_rq
    return None


def latest_component_version_from_upstream(
    component_name: str,
    upstream_component_name: str,
    ctx_repo: gci.componentmodel.OciRepositoryContext,
    ignore_prerelease_versions: bool=False,
):
    upstream_component_version = cnudie.retrieve.greatest_component_version(
        component_name=upstream_component_name,
        ctx_repo=ctx_repo,
        ignore_prerelease_versions=ignore_prerelease_versions,
    )

    if not upstream_component_version:
        raise RuntimeError(
            f'did not find any versions for {upstream_component_name=}, {ctx_repo=}'
        )

    upstream_component_descriptor = component_descriptor_lookup()(
        component_id=gci.componentmodel.ComponentIdentity(
            name=upstream_component_name,
            version=upstream_component_version,
        ),
        ctx_repo=ctx_repo,
    )
    upstream_component = upstream_component_descriptor.component
    for component_ref in upstream_component.componentReferences:
        # TODO: Validate that component_name is unique
        if component_ref.componentName == component_name:
            return component_ref.version


def determine_reference_versions(
    component_name: str,
    reference_version: str,
    ctx_repo: gci.componentmodel.OciRepositoryContext,
    upstream_component_name: str=None,
    upstream_update_policy: UpstreamUpdatePolicy=UpstreamUpdatePolicy.STRICTLY_FOLLOW,
    ignore_prerelease_versions: bool=False,
) -> typing.Sequence[str]:
    if upstream_component_name is None:
        # no upstream component defined - look for greatest released version
        latest_component_version = cnudie.retrieve.greatest_component_version(
            component_name=component_name,
            ctx_repo=ctx_repo,
            ignore_prerelease_versions=ignore_prerelease_versions,
        )
        if not latest_component_version:
            raise RuntimeError(
                f'did not find any versions of {component_name=} {ctx_repo=}'
            )

        return (
            latest_component_version,
        )

    version_candidate = latest_component_version_from_upstream(
        component_name=component_name,
        upstream_component_name=upstream_component_name,
        ctx_repo=ctx_repo,
        ignore_prerelease_versions=ignore_prerelease_versions,
    )

    if upstream_update_policy is UpstreamUpdatePolicy.STRICTLY_FOLLOW:
        return (version_candidate,)

    elif upstream_update_policy is UpstreamUpdatePolicy.ACCEPT_HOTFIXES:
        hotfix_candidate = cnudie.retrieve.greatest_component_version_with_matching_minor(
            component_name=component_name,
            ctx_repo=ctx_repo,
            reference_version=reference_version,
            ignore_prerelease_versions=ignore_prerelease_versions,
        )
        return (hotfix_candidate, version_candidate)

    else:
        raise NotImplementedError


def determine_upgrade_prs(
    upstream_component_name: str,
    upstream_update_policy: UpstreamUpdatePolicy,
    upgrade_pull_requests: typing.Iterable[github.util.UpgradePullRequest],
    ctx_repo: gci.componentmodel.OciRepositoryContext,
    ignore_prerelease_versions=False,
) -> typing.Iterable[typing.Tuple[
    gci.componentmodel.ComponentReference, gci.componentmodel.ComponentReference, str
]]:
    for greatest_component_reference in product.v2.greatest_references(
        references=current_component().componentReferences,
    ):
        for greatest_version in determine_reference_versions(
            component_name=greatest_component_reference.componentName,
            reference_version=greatest_component_reference.version,
            upstream_component_name=upstream_component_name,
            upstream_update_policy=upstream_update_policy,
            ctx_repo=ctx_repo,
            ignore_prerelease_versions=ignore_prerelease_versions,
        ):
            if not greatest_version:
                # if None is returned, no versions at all were found
                print(
                    'Warning: no component versions found for '
                    f'{greatest_component_reference.componentName=}'
                )
                continue

            greatest_version_semver = version.parse_to_semver(greatest_version)
            print(f'{greatest_version=}, ours: {greatest_component_reference} {ctx_repo=}')
            if greatest_version_semver <= version.parse_to_semver(
                greatest_component_reference.version
            ):
                logger.info(
                    f'skipping (outdated) {greatest_component_reference=}; '
                    f'our {greatest_component_reference.version=}, '
                    f'found: {greatest_version=}'
                )
                continue
            elif matching_pr := upgrade_pr_exists(
                component_reference=greatest_component_reference,
                component_version=greatest_version,
                upgrade_requests=upgrade_pull_requests,
            ):
                logger.info(
                    'skipping upgrade (PR already exists): '
                    f'{greatest_component_reference=} '
                    f'to {greatest_version=} ({matching_pr.pull_request.html_url})'
                )
                continue
            else:
                yield(greatest_component_reference, greatest_version)


def _import_release_notes(
    component: gci.componentmodel.Component,
    to_version: str,
    pull_request_util,
):
    if not component.sources:
        logger.warning(
            f'''
            {component.name=}:{component.version=} has no sources; skipping release-notes-import
            '''
        )
        return None

    main_source = cnudie.util.determine_main_source_for_component(component)
    github_cfg = ccc.github.github_cfg_for_repo_url(main_source.access.repoUrl)
    org_name = main_source.access.org_name()
    repository_name = main_source.access.repository_name()

    release_notes = create_release_notes(
        from_component=component,
        from_github_cfg=github_cfg,
        from_repo_owner=org_name,
        from_repo_name=repository_name,
        to_version=to_version,
    )

    if not release_notes:
        release_notes = pull_request_util.retrieve_pr_template_text()

    return release_notes


def create_upgrade_pr(
    component: gci.componentmodel.Component,
    from_ref: gci.componentmodel.ComponentReference,
    to_ref: gci.componentmodel.ComponentReference,
    to_version: str,
    pull_request_util: github.util.PullRequestUtil,
    upgrade_script_path,
    upgrade_script_relpath,
    githubrepobranch: GitHubRepoBranch,
    repo_dir,
    github_cfg_name,
    merge_policy: MergePolicy,
    merge_method: MergeMethod,
    after_merge_callback=None,
    container_image:str=None,
):
    if container_image:
        dockerutil.launch_dockerd_if_not_running()

    ls_repo = pull_request_util.repository

    from_component_descriptor = component_descriptor_lookup()(
        component_id=gci.componentmodel.ComponentIdentity(
            name=from_ref.componentName,
            version=from_ref.version,
        ),
        ctx_repo=component.current_repository_ctx(),
    )
    from_component = from_component_descriptor.component

    # prepare env for upgrade script and after-merge-callback
    cmd_env = os.environ.copy()
    # TODO: Handle upgrades for types other than 'component'
    cmd_env['DEPENDENCY_TYPE'] = product.v2.COMPONENT_TYPE_NAME
    cmd_env['DEPENDENCY_NAME'] = to_ref.componentName
    cmd_env['LOCAL_DEPENDENCY_NAME'] = to_ref.name
    cmd_env['DEPENDENCY_VERSION'] = to_version
    if container_image:
        cmd_env['REPO_DIR'] = (repo_dir_in_container := '/mnt/main_repo')
    else:
        cmd_env['REPO_DIR'] = repo_dir
    cmd_env['GITHUB_CFG_NAME'] = github_cfg_name
    cmd_env['CTX_REPO_URL'] = component.current_repository_ctx().baseUrl

    if not container_image:
        # create upgrade diff
        subprocess.run(
            [str(upgrade_script_path)],
            check=True,
            env=cmd_env
        )
    else:
        # run check-script in container
        oci_registry_cfg = cr.find_config(image_reference=container_image)
        if oci_registry_cfg:
            docker_cfg_dir = tempfile.TemporaryDirectory()
            dockerutil.mk_docker_cfg_dir(
                cfg={'auths': oci_registry_cfg.as_docker_auths()},
                cfg_dir=docker_cfg_dir.name,
                exist_ok=True,
            )
        else:
            docker_cfg_dir = None

        upgrade_script_path_in_container = os.path.join(
            repo_dir_in_container,
            upgrade_script_relpath,
        )

        docker_argv = dockerutil.docker_run_argv(
            image_reference=container_image,
            argv=(
                upgrade_script_path_in_container,
            ),
            env=cmd_env,
            mounts={
                repo_dir: repo_dir_in_container,
            },
            cfg_dir=docker_cfg_dir.name,
        )

        logger.info(f'will run: ${docker_argv=}')

        try:
            subprocess.run(
                docker_argv,
                check=True,
            )
        finally:
            if docker_cfg_dir:
                docker_cfg_dir.cleanup()

    from_version = from_ref.version
    commit_message = f'Upgrade {to_ref.name}\n\nfrom {from_version} to {to_version}'

    upgrade_branch_name = push_upgrade_commit(
        ls_repo=ls_repo,
        commit_message=commit_message,
        githubrepobranch=githubrepobranch,
        repo_dir=repo_dir,
    )
    # branch was created. Cleanup if something fails
    try:
        release_notes = _import_release_notes(
            component=from_component,
            to_version=to_version,
            pull_request_util=pull_request_util,
        )
    except Exception:
        logger.warning('failed to retrieve release-notes')
        traceback.print_exc()
        release_notes = 'failed to retrieve release-notes'

    pull_request = ls_repo.create_pull(
        title=github.util.PullRequestUtil.calculate_pr_title(
            reference=to_ref,
            from_version=from_version,
            to_version=to_version
        ),
        base=githubrepobranch.branch(),
        head=upgrade_branch_name,
        body=release_notes or 'failed to retrieve release-notes',
    )

    if merge_policy is MergePolicy.MANUAL:
        return

    if merge_method is MergeMethod.MERGE:
        pull_request.merge(merge_method='merge')
    elif merge_method is MergeMethod.REBASE:
        pull_request.merge(merge_method='rebase')
    elif merge_method is MergeMethod.SQUASH:
        pull_request.merge(merge_method='squash')
    else:
        raise NotImplementedError(f'{merge_method=}')

    try:
        ls_repo.ref(f'heads/{upgrade_branch_name}').delete()
    except github3.exceptions.NotFoundError:
        pass

    if after_merge_callback:
        subprocess.run(
            [os.path.join(repo_dir, after_merge_callback)],
            check=True,
            env=cmd_env
        )


def push_upgrade_commit(
    ls_repo: github3.repos.repo.Repository,
    commit_message: str,
    githubrepobranch: GitHubRepoBranch,
    repo_dir: str,
) -> str:
    # mv diff into commit and push it
    helper = gitutil.GitHelper.from_githubrepobranch(
        githubrepobranch=githubrepobranch,
        repo_path=repo_dir,
    )
    commit = helper.index_to_commit(message=commit_message)
    logger.info(f'commit for upgrade-PR: {commit.hexsha=}')
    new_branch_name = ci.util.random_str(prefix='ci-', length=12)
    repo_branch = githubrepobranch.branch()
    head_sha = ls_repo.ref(f'heads/{repo_branch}').object.sha
    ls_repo.create_ref(f'refs/heads/{new_branch_name}', head_sha)

    try:
        helper.push(from_ref=commit.hexsha, to_ref=f'refs/heads/{new_branch_name}')
    except:
        logger.warning('an error occurred - removing now useless pr-branch')
        ls_repo.ref(f'heads/{new_branch_name}').delete()
        raise

    helper.repo.git.checkout('.')

    return new_branch_name


def create_release_notes(
    from_component: gci.componentmodel.Component,
    from_github_cfg,
    from_repo_owner: str,
    from_repo_name: str,
    to_version: str,
):
    from_version = from_component.version
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            gitutil.GitHelper.clone_into(
                target_directory=temp_dir,
                github_cfg=from_github_cfg,
                github_repo_path=f'{from_repo_owner}/{from_repo_name}'
            )
            commit_range = '{from_version}..{to_version}'.format(
                from_version=from_version,
                to_version=to_version,
            )
            release_notes = ReleaseNotes(
                component=from_component,
                repo_dir=temp_dir,
            )
            release_notes.create(
                start_ref=None, # the repo's default branch
                commit_range=commit_range
            )
            release_note_blocks = release_notes.release_note_blocks()
            if release_note_blocks:
                return f'**Release Notes**:\n{release_note_blocks}'
    except:
        logger.warning('an error occurred during release notes processing (ignoring)')
        import traceback
        logger.warning(traceback.format_exc())
