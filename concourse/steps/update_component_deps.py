import logging
import os
import subprocess
import tempfile
import typing

import gci.componentmodel
import github3.exceptions

import ccc.github
import ci.util
import cnudie.util
import cnudie.retrieve
import concourse.model.traits.update_component_deps
import concourse.steps.component_descriptor_util as cdu
import github.util
import gitutil
import product.v2
import version
from concourse.model.traits.update_component_deps import (
    MergePolicy,
)
from github.util import (
    GitHubRepoBranch,
)
from github.release_notes.util import ReleaseNotes

logger = logging.getLogger('step.update_component_deps')


UpstreamUpdatePolicy = concourse.model.traits.update_component_deps.UpstreamUpdatePolicy


def current_product_descriptor():
    return gci.componentmodel.ComponentDescriptor.from_dict(
        component_descriptor_dict=ci.util.parse_yaml_file(
            os.path.join(
                ci.util.check_env('COMPONENT_DESCRIPTOR_DIR'),
                cdu.component_descriptor_fname(gci.componentmodel.SchemaVersion.V2),
            )
        )
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
    upgrade_requests,
):
    return any(
        [
            upgrade_rq.target_matches(
                reference=component_reference,
                reference_version=component_version,
            )
            for upgrade_rq in upgrade_requests
        ]
    )


def get_source_repo_config_for_component_reference(
    component: gci.componentmodel.Component,
    component_reference: gci.componentmodel.ComponentReference,
    component_version: str,
):
    component_descriptor = cnudie.retrieve.component_descriptor(
        name=component_reference.componentName,
        version=component_reference.version,
        ctx_repo_url=component.current_repository_ctx().baseUrl,
    )
    resolved_component = component_descriptor.component
    if not resolved_component.sources:
        raise ValueError(f'{resolved_component.name=} has no sources')

    main_source = cnudie.util.determine_main_source_for_component(resolved_component)

    return (
        ccc.github.github_cfg_for_hostname(main_source.access.hostname()),
        main_source.access.org_name(),
        main_source.access.repository_name(),
    )


def latest_component_version_from_upstream(
    component_name: str,
    upstream_component_name: str,
    base_url: str,
):
    upstream_component_version = product.v2.latest_component_version(
        component_name=upstream_component_name,
        ctx_repo_base_url=base_url,
    )

    if not upstream_component_version:
        raise RuntimeError(
            f'did not find any versions for {upstream_component_name=}, {base_url=}'
        )

    upstream_component_descriptor = cnudie.retrieve.component_descriptor(
        name=upstream_component_name,
        version=upstream_component_version,
        ctx_repo_url=base_url,
    )
    upstream_component = upstream_component_descriptor.component
    for component_ref in upstream_component.componentReferences:
        # TODO: Validate that component_name is unique
        if component_ref.componentName == component_name:
            return component_ref.version


def determine_reference_versions(
    component_name: str,
    reference_version: str,
    repository_ctx_base_url: str,
    upstream_component_name: str=None,
    upstream_update_policy: UpstreamUpdatePolicy=UpstreamUpdatePolicy.STRICTLY_FOLLOW,
) -> typing.Sequence[str]:
    if upstream_component_name is None:
        # no upstream component defined - look for greatest released version
        latest_component_version = product.v2.latest_component_version(
                component_name=component_name,
                ctx_repo_base_url=repository_ctx_base_url,
        )
        if not latest_component_version:
            raise RuntimeError(
                f'did not find any versions of {component_name=} {repository_ctx_base_url=}'
            )

        return (
            latest_component_version,
        )

    version_candidate = latest_component_version_from_upstream(
        component_name=component_name,
        upstream_component_name=upstream_component_name,
        base_url=repository_ctx_base_url,
    )

    if upstream_update_policy is UpstreamUpdatePolicy.STRICTLY_FOLLOW:
        return (version_candidate,)

    elif upstream_update_policy is UpstreamUpdatePolicy.ACCEPT_HOTFIXES:
        hotfix_candidate = product.v2.greatest_component_version_with_matching_minor(
            component_name=component_name,
            ctx_repo_base_url=repository_ctx_base_url,
            reference_version=reference_version,
        )
        return (hotfix_candidate, version_candidate)

    else:
        raise NotImplementedError


def determine_upgrade_prs(
    upstream_component_name: str,
    upstream_update_policy: UpstreamUpdatePolicy,
    upgrade_pull_requests,
    ctx_repo_base_url: str,
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
            repository_ctx_base_url=ctx_repo_base_url,
        ):
            if not greatest_version:
                # if None is returned, no versions at all were found
                print(
                    'Warning: no component versions found for '
                    f'{greatest_component_reference.componentName=}'
                )
                continue

            greatest_version_semver = version.parse_to_semver(greatest_version)
            print(f'{greatest_version=}, ours: {greatest_component_reference} {ctx_repo_base_url=}')
            if greatest_version_semver <= version.parse_to_semver(
                greatest_component_reference.version
            ):
                logger.info(
                    f'skipping (outdated) {greatest_component_reference=}; '
                    f'our {greatest_component_reference.version=}, '
                    f'found: {greatest_version=}'
                )
                continue
            elif upgrade_pr_exists(
                component_reference=greatest_component_reference,
                component_version=greatest_version,
                upgrade_requests=upgrade_pull_requests,
            ):
                logger.info(
                    'skipping upgrade (PR already exists): '
                    f'{greatest_component_reference=} '
                    f'to {greatest_version=}'
                )
                continue
            else:
                yield(greatest_component_reference, greatest_version)


def create_upgrade_pr(
    component: gci.componentmodel.Component,
    from_ref: gci.componentmodel.ComponentReference,
    to_ref: gci.componentmodel.ComponentReference,
    to_version: str,
    pull_request_util,
    upgrade_script_path,
    githubrepobranch: GitHubRepoBranch,
    repo_dir,
    github_cfg_name,
    cfg_factory,
    merge_policy,
    after_merge_callback=None,
):
    ls_repo = pull_request_util.repository
    from_version = from_ref.version

    # prepare env for upgrade script and after-merge-callback
    cmd_env = os.environ.copy()
    # TODO: Handle upgrades for types other than 'component'
    cmd_env['DEPENDENCY_TYPE'] = product.v2.COMPONENT_TYPE_NAME
    cmd_env['DEPENDENCY_NAME'] = to_ref.componentName
    cmd_env['LOCAL_DEPENDENCY_NAME'] = to_ref.name
    cmd_env['DEPENDENCY_VERSION'] = to_version
    cmd_env['REPO_DIR'] = repo_dir
    cmd_env['GITHUB_CFG_NAME'] = github_cfg_name
    cmd_env['CTX_REPO_URL'] = component.current_repository_ctx().baseUrl

    # create upgrade diff
    subprocess.run(
        [str(upgrade_script_path)],
        check=True,
        env=cmd_env
    )

    commit_message = f'Upgrade {to_ref.name}\n\nfrom {from_version} to {to_version}'

    upgrade_branch_name = push_upgrade_commit(
        ls_repo=ls_repo,
        commit_message=commit_message,
        githubrepobranch=githubrepobranch,
        repo_dir=repo_dir,
    )

    github_cfg, repo_owner, repo_name = get_source_repo_config_for_component_reference(
        component=component,
        component_reference=from_ref,
        component_version=from_version,
    )

    release_notes = create_release_notes(
        from_component_ref=from_ref,
        ctx_repo_base_url=component.current_repository_ctx().baseUrl,
        from_github_cfg=github_cfg,
        from_repo_owner=repo_owner,
        from_repo_name=repo_name,
        from_version=from_version,
        to_version=to_version,
    )

    if not release_notes:
        release_notes = pull_request_util.retrieve_pr_template_text()

    pull_request = ls_repo.create_pull(
        title=github.util.PullRequestUtil.calculate_pr_title(
            reference=to_ref,
            from_version=from_version,
            to_version=to_version
        ),
        base=githubrepobranch.branch(),
        head=upgrade_branch_name,
        body=release_notes,
    )

    if merge_policy is MergePolicy.MANUAL:
        return
    # auto-merge - todo: make configurable (e.g. merge method)
    pull_request.merge()
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
    ls_repo,
    commit_message: str,
    githubrepobranch,
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
    from_component_ref,
    ctx_repo_base_url,
    from_github_cfg,
    from_repo_owner: str,
    from_repo_name: str,
    from_version: str,
    to_version: str,
):
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            gitutil.GitHelper.clone_into(
                target_directory=temp_dir,
                github_cfg=from_github_cfg,
                github_repo_path=f'{from_repo_owner}/{from_repo_name}'
            )
            from_cd = product.v2.download_component_descriptor_v2(
                component_name=from_component_ref.componentName,
                component_version=from_component_ref.version,
                ctx_repo_base_url=ctx_repo_base_url,
            )
            commit_range = '{from_version}..{to_version}'.format(
                from_version=from_version,
                to_version=to_version,
            )
            release_notes = ReleaseNotes(
                component=from_cd.component,
                repo_dir=temp_dir,
            )
            release_notes.create(
                start_ref=None, # the repo's default branch
                commit_range=commit_range
            )
            release_note_blocks = release_notes.release_note_blocks()
            if release_note_blocks:
                return f'**Release Notes*:\n{release_note_blocks}'
    except:
        logger.warning('an error occurred during release notes processing (ignoring)')
        import traceback
        logger.warning(traceback.format_exc())
