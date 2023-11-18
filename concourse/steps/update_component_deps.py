import logging
import os
import subprocess
import tempfile
import time
import traceback
import typing

import gci.componentmodel
import github3.exceptions
import github3.repos.repo

import ccc.github
import ci.util
import cnudie.retrieve
import cnudie.util
import concourse.model.traits.update_component_deps
import concourse.paths
import concourse.steps.component_descriptor_util as cdu
import dockerutil
import github.util
import gitutil
import model.container_registry as cr
import release_notes.fetch as release_notes_fetch
import version
from concourse.model.traits.update_component_deps import (
    MergePolicy,
    MergeMethod,
)
from github.util import (
    GitHubRepoBranch,
)

logger = logging.getLogger('step.update_component_deps')


UpstreamUpdatePolicy = concourse.model.traits.update_component_deps.UpstreamUpdatePolicy


def current_product_descriptor():
    component_descriptor_file_path = cdu.component_descriptor_path(
        schema_version=gci.componentmodel.SchemaVersion.V2,
    )

    # cd is supplied via component-descriptor file. Parse and return
    if os.path.isfile(component_descriptor_file_path):
        return gci.componentmodel.ComponentDescriptor.from_dict(
            component_descriptor_dict=ci.util.parse_yaml_file(component_descriptor_file_path,)
        )
    else:
        raise RuntimeError(f'did not find component-descriptor at {component_descriptor_file_path=}')


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
    request_filter: typing.Callable[[github.util.UpgradePullRequest], bool] = lambda rq: True,
) -> github.util.UpgradePullRequest | None:
    if any(
        (matching_rq := upgrade_rq).target_matches(
            reference=component_reference,
            reference_version=component_version,
        ) and request_filter(upgrade_rq)
        for upgrade_rq in upgrade_requests
    ):
        return matching_rq
    return None


def greatest_component_version(
    component_name,
    version_lookup,
    ignore_prerelease_versions,
) -> str | None:
    versions = version_lookup(
        gci.componentmodel.ComponentIdentity(
            name=component_name,
            version='dont_care',
        )
    )
    if not versions:
        return None

    greatest_version = version.greatest_version(
        versions=versions,
        ignore_prerelease_versions=ignore_prerelease_versions,
    )

    if not greatest_version:
        return None

    return greatest_version


def greatest_component_version_with_matching_minor(
    component_name,
    version_lookup,
    reference_version,
    ignore_prerelease_versions,
) -> str | None:
    versions = version_lookup(
        gci.componentmodel.ComponentIdentity(
            name=component_name,
            version='dont_care',
        )
    )
    if not versions:
        return None

    greatest_version = version.greatest_version_with_matching_minor(
        reference_version=reference_version,
        versions=versions,
        ignore_prerelease_versions=ignore_prerelease_versions,
    )

    if not greatest_version:
        return None

    return greatest_version


def latest_component_version_from_upstream(
    component_name: str,
    upstream_component_name: str,
    ocm_lookup,
    version_lookup,
    ignore_prerelease_versions: bool=False,
):
    upstream_component_version = greatest_component_version(
        component_name=upstream_component_name,
        version_lookup=version_lookup,
        ignore_prerelease_versions=ignore_prerelease_versions,
    )

    if not upstream_component_version:
        raise RuntimeError(
            f'did not find any versions for {upstream_component_name=}'
        )

    upstream_component_descriptor = ocm_lookup(
        gci.componentmodel.ComponentIdentity(
            name=upstream_component_name,
            version=upstream_component_version,
        ),
    )

    upstream_component = upstream_component_descriptor.component
    for component_ref in upstream_component.componentReferences:
        # TODO: Validate that component_name is unique
        if component_ref.componentName == component_name:
            return component_ref.version


def determine_reference_versions(
    component_name: str,
    reference_version: str,
    version_lookup,
    ocm_lookup,
    upstream_component_name: str=None,
    upstream_update_policy: UpstreamUpdatePolicy=UpstreamUpdatePolicy.STRICTLY_FOLLOW,
    ignore_prerelease_versions: bool=False,
) -> typing.Sequence[str]:
    if upstream_component_name is None:
        # no upstream component defined - look for greatest released version
        latest_component_version = greatest_component_version(
            component_name=component_name,
            version_lookup=version_lookup,
            ignore_prerelease_versions=ignore_prerelease_versions,
        )
        if not latest_component_version:
            raise RuntimeError(
                f'did not find any versions of {component_name=}'
            )

        return (
            latest_component_version,
        )

    version_candidate = latest_component_version_from_upstream(
        component_name=component_name,
        upstream_component_name=upstream_component_name,
        version_lookup=version_lookup,
        ocm_lookup=ocm_lookup,
        ignore_prerelease_versions=ignore_prerelease_versions,
    )

    if upstream_update_policy is UpstreamUpdatePolicy.STRICTLY_FOLLOW:
        return (version_candidate,)

    elif upstream_update_policy is UpstreamUpdatePolicy.ACCEPT_HOTFIXES:
        hotfix_candidate = greatest_component_version_with_matching_minor(
            component_name=component_name,
            version_lookup=version_lookup,
            reference_version=reference_version,
            ignore_prerelease_versions=ignore_prerelease_versions,
        )
        if hotfix_candidate == version_candidate:
            return (version_candidate,)
        else:
            return (hotfix_candidate, version_candidate)

    else:
        raise NotImplementedError


def greatest_references(
    references: typing.Iterable[gci.componentmodel.ComponentReference],
) -> typing.Iterable[gci.componentmodel.ComponentReference]:
    '''
    yields the component references from the specified iterable of ComponentReference that
    have the greatest version (grouped by component name).
    Id est: if the sequence contains exactly one version of each contained component name,
    the sequence is returned unchanged.
    '''
    references = tuple(references)
    names = {r.name for r in references}

    for name in names:
        matching_refs = [r for r in references if r.name == name]
        if len(matching_refs) == 1:
            # in case reference name was unique, do not bother sorting
            # (this also works around issues from non-semver versions)
            yield matching_refs[0]
        else:
            # there might be multiple component versions of the same name
            # --> use the greatest version in that case
            matching_refs.sort(key=lambda r: version.parse_to_semver(r.version))
            # greates version comes last
            yield matching_refs[-1]


def determine_upgrade_prs(
    upstream_component_name: str,
    upstream_update_policy: UpstreamUpdatePolicy,
    upgrade_pull_requests: typing.Iterable[github.util.UpgradePullRequest],
    version_lookup,
    ocm_lookup,
    ignore_prerelease_versions=False,
) -> typing.Iterable[typing.Tuple[
    gci.componentmodel.ComponentReference, gci.componentmodel.ComponentReference, str
]]:
    for greatest_component_reference in greatest_references(
        references=current_component().componentReferences,
    ):
        versions_to_consider = determine_reference_versions(
            component_name=greatest_component_reference.componentName,
            reference_version=greatest_component_reference.version,
            upstream_component_name=upstream_component_name,
            upstream_update_policy=upstream_update_policy,
            version_lookup=version_lookup,
            ocm_lookup=ocm_lookup,
            ignore_prerelease_versions=ignore_prerelease_versions,
        )
        if versions_to_consider:
            logger.info(
                f"Found possible version(s) to up- or downgrade to: '{versions_to_consider}' for "
                f'{greatest_component_reference.componentName=}'
            )
        else:
            logger.warning(
                f'No component versions found for {greatest_component_reference.componentName=}'
            )
        for candidate_version in versions_to_consider:
            # we might have found 'None' as version to consider.
            if not candidate_version:
                continue

            candidate_version_semver = version.parse_to_semver(candidate_version)
            reference_version_semver = version.parse_to_semver(greatest_component_reference.version)

            logger.info(f'{candidate_version=}, ours: {greatest_component_reference}')

            if candidate_version_semver <= reference_version_semver:
                downgrade_pr = True
                # downgrades are permitted iff the version is tracking a _dependency_ of another
                # component and we are to follow strictly
                if (
                    candidate_version_semver == reference_version_semver or
                    not upstream_component_name
                    or upstream_update_policy is not UpstreamUpdatePolicy.STRICTLY_FOLLOW
                ):
                    logger.info(
                        f'skipping (outdated) {greatest_component_reference=}; '
                        f'our {greatest_component_reference.version=}, '
                        f'found: {candidate_version=}'
                    )
                    continue
            else:
                downgrade_pr = False

            if not downgrade_pr and (matching_pr := upgrade_pr_exists(
                component_reference=greatest_component_reference,
                component_version=candidate_version,
                upgrade_requests=upgrade_pull_requests,
                request_filter=lambda rq: not rq.is_downgrade(),
            )):
                logger.info(
                    'skipping upgrade (PR already exists): '
                    f'{greatest_component_reference=} '
                    f'to {candidate_version=} ({matching_pr.pull_request.html_url})'
                )
                continue
            elif downgrade_pr and (matching_pr := upgrade_pr_exists(
                component_reference=greatest_component_reference,
                component_version=candidate_version,
                upgrade_requests=upgrade_pull_requests,
                request_filter=lambda rq: rq.is_downgrade(),
            )):
                logger.info(
                    'skipping downgrade (PR already exists): '
                    f'{greatest_component_reference=} '
                    f'to {candidate_version=} ({matching_pr.pull_request.html_url})'
                )
                continue
            else:
                yield(greatest_component_reference, candidate_version)


def _import_release_notes(
    component: gci.componentmodel.Component,
    to_version: str,
    pull_request_util,
    version_lookup,
    component_descriptor_lookup,
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
        version_lookup=version_lookup,
        component_descriptor_lookup=component_descriptor_lookup,
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
    version_lookup,
    component_descriptor_lookup,
    after_merge_callback=None,
    container_image:str=None,
) -> github.util.UpgradePullRequest:
    if container_image:
        dockerutil.launch_dockerd_if_not_running()

    ls_repo = pull_request_util.repository

    from_component_descriptor = component_descriptor_lookup(
        gci.componentmodel.ComponentIdentity(
            name=from_ref.componentName,
            version=from_ref.version,
        ),
        absent_ok=False,
    )
    from_component = from_component_descriptor.component

    # prepare env for upgrade script and after-merge-callback
    cmd_env = os.environ.copy()
    # TODO: Handle upgrades for types other than 'component'
    cmd_env['DEPENDENCY_TYPE'] = 'component'
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
            version_lookup=version_lookup,
            component_descriptor_lookup=component_descriptor_lookup,
        )
    except Exception:
        logger.warning('failed to retrieve release-notes')
        traceback.print_exc()
        release_notes = 'failed to retrieve release-notes'

    if release_notes:
        max_pr_body_length = 65536 # also: max comment body length
        # If the size of the release-notes exceeds the max. body-length for PRs, split the notes
        # into MAX_PR_BODY_LENGTH-sized chunks and add subsequent chunks to the PR as comments.
        max_body_length_exceeded_remark = (
            '\n\nRelease notes were shortened since they exceeded the maximum length allowed for a '
            'pull request body. The remaining release notes will be added as comments to this PR.'
        )
        if max_pr_body_length < len(release_notes):
            step_size = max_pr_body_length - len(max_body_length_exceeded_remark)
            split_release_notes = [
                release_notes[start:start+step_size]
                for start in range(0, len(release_notes), step_size)
            ]
        else:
            split_release_notes = [release_notes]

        if not (additional_notes := split_release_notes[1:]):
            pr_body = split_release_notes[0]
        else:
            pr_body = split_release_notes[0] + max_body_length_exceeded_remark
    else:
        pr_body = None
        additional_notes = []

    try:
        pull_request = ls_repo.create_pull(
            title=github.util.PullRequestUtil.calculate_pr_title(
                reference=to_ref,
                from_version=from_version,
                to_version=to_version
            ),
            base=githubrepobranch.branch(),
            head=upgrade_branch_name,
            body=pr_body,
        )

        for release_note_part in additional_notes:
            pull_request.create_comment(body=release_note_part)
    except github3.exceptions.UnprocessableEntity as e:
        logger.info(f'Intercepted UnprocessableEntity exception. Listed errors: {e.errors}')
        raise

    if merge_policy is MergePolicy.MANUAL:
        return pull_request_util._pr_to_upgrade_pull_request(pull_request)

    logger.info(
        f"Merging upgrade-pr #{pull_request.number} ({merge_method=!s}) on branch "
        f"'{upgrade_branch_name}' into branch '{githubrepobranch.branch()}'."
    )

    def  _merge_pr(
        merge_method: MergeMethod,
        pull_request: github3.github.pulls.ShortPullRequest,
        attempts: int,
        delay: int = 2,
    ):
        if attempts > 0:
            try:
                if merge_method is MergeMethod.MERGE:
                    pull_request.merge(merge_method='merge')
                elif merge_method is MergeMethod.REBASE:
                    pull_request.merge(merge_method='rebase')
                elif merge_method is MergeMethod.SQUASH:
                    pull_request.merge(merge_method='squash')
                else:
                    raise NotImplementedError(f'{merge_method=}')
            except github3.exceptions.MethodNotAllowed as e:
                remaining_attempts = attempts-1
                logger.warning(
                    f'Encountered an exception when merging PR: {e}. Will wait {delay} seconds '
                    f'and try again {remaining_attempts} time(s).'
                )
                time.sleep(delay)
                _merge_pr(
                    merge_method=merge_method,
                    pull_request=pull_request,
                    attempts=remaining_attempts,
                )
        else:
            logger.warning(
                f'Unable to merge upgrade pull request #{pull_request.number} '
                f'({pull_request.html_url}).'
            )

    _merge_pr(merge_method=merge_method, pull_request=pull_request, attempts=3)

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

    return pull_request_util._pr_to_upgrade_pull_request(pull_request)


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
    version_lookup,
    component_descriptor_lookup,
):
    from_version = from_component.version

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            gitutil.GitHelper.clone_into(
                target_directory=temp_dir,
                github_cfg=from_github_cfg,
                github_repo_path=f'{from_repo_owner}/{from_repo_name}'
            )
            release_note_blocks = release_notes_fetch.fetch_release_notes(
                component=from_component,
                version_lookup=version_lookup,
                component_descriptor_lookup=component_descriptor_lookup,
                repo_path=temp_dir,
                current_version=to_version,
                previous_version=from_version,
            )
            if release_note_blocks:
                n = '\n'
                return f'**Release Notes**:\n{n.join(r.block_str for r in release_note_blocks)}'

    except:
        logger.warning('an error occurred during release notes processing (ignoring)')
        import traceback
        logger.warning(traceback.format_exc())
