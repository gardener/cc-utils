import dataclasses
import logging
import os
import subprocess
import tempfile
import typing
import version

import yaml

from github3.exceptions import (
    ConnectionError,
)
from git.exc import (
    GitCommandError
)
import git.types

import ocm
import ocm.gardener

import ccc.github
import concourse.steps.version
import concourse.model.traits.version as version_trait
import dockerutil
import github.util
import release_notes.fetch
import release_notes.markdown
import release_notes.ocm
import slackclient.util

from gitutil import GitHelper
from concourse.model.traits.release import (
    ReleaseCommitPublishingPolicy,
)
import model.container_registry as cr

logger = logging.getLogger('step.release')


def _invoke_callback(
    callback_script_path: str,
    repo_dir: str,
    effective_version: str,
    callback_image_reference: str=None,
):
    callback_env = os.environ.copy()
    callback_env['EFFECTIVE_VERSION'] = effective_version

    if callback_image_reference:
        repo_dir_in_container = '/mnt/main_repo'
        callback_env['REPO_DIR'] = repo_dir_in_container
    else:
        callback_env['REPO_DIR'] = repo_dir

    if not callback_image_reference:
        callback_script_path = os.path.join(
            repo_dir,
            callback_script_path,
        )
        subprocess.run(
            [callback_script_path],
            check=True,
            env=callback_env,
        )
    else:
        script_path_in_container = os.path.join(
            repo_dir_in_container,
            callback_script_path,
        )

        oci_registry_cfg = cr.find_config(image_reference=callback_image_reference)
        if oci_registry_cfg:
            docker_cfg_dir = tempfile.TemporaryDirectory()
            dockerutil.mk_docker_cfg_dir(
                cfg={'auths': oci_registry_cfg.as_docker_auths()},
                cfg_dir=docker_cfg_dir.name,
                exist_ok=True,
            )
        else:
            docker_cfg_dir = None

        docker_argv = dockerutil.docker_run_argv(
            image_reference=callback_image_reference,
            argv=(script_path_in_container,),
            env=callback_env,
            mounts={
                repo_dir: repo_dir_in_container,
            },
            cfg_dir=docker_cfg_dir.name,
        )

        dockerutil.launch_dockerd_if_not_running()

        logger.info(f'will run callback using {docker_argv=}')

        try:
            subprocess.run(
                docker_argv,
                check=True,
            )
        finally:
            if docker_cfg_dir:
                docker_cfg_dir.cleanup()


def _calculate_next_cycle_dev_version(
    release_version: str,
    version_operation: str,
    prerelease_suffix: str,
):
    # calculate the next version and append the prerelease suffix
    return version.process_version(
        version_str=version.process_version(
            version_str=release_version,
            operation=version_operation,
        ),
        operation='set_prerelease',
        prerelease=prerelease_suffix,
    )


def _calculate_tags(
    version: str,
    github_release_tag: dict,
    git_tags: list,
) -> typing.Sequence[str]:
    github_release_tag_candidate = github_release_tag['ref_template'].format(
        VERSION=version,
    )
    git_tag_candidates = [
        tag_template['ref_template'].format(VERSION=version)
        for tag_template in git_tags
    ]

    return [github_release_tag_candidate] + git_tag_candidates


def collect_release_notes(
    git_helper: GitHelper,
    release_version: str,
    component,
    component_descriptor_lookup,
    version_lookup,
    oci_client,
) -> tuple[str, str]:
    release_note_blocks = release_notes.fetch.fetch_release_notes(
        component=component,
        component_descriptor_lookup=component_descriptor_lookup,
        version_lookup=version_lookup,
        git_helper=git_helper,
        github_api_lookup=ccc.github.github_api_lookup,
        version_whither=release_version,
    )

    release_notes_markdown = '\n'.join(
        str(i) for i in release_notes.markdown.render(release_note_blocks)
    ) or ''

    version_vector = ocm.gardener.UpgradeVector(
        whence=ocm.ComponentIdentity(
            name=component.name,
            version=version.find_predecessor(
                version=component.version,
                versions=[v for v in version_lookup(component) if version.is_final(v)],
            ),
        ),
        whither=component,
    )

    # retrieve release-notes from sub-components
    sub_component_release_notes = '\n'.join((
        release_notes.ocm.release_notes_markdown_with_heading(cid, rn)
        for cid, rn in release_notes.ocm.release_notes_range_recursive(
            version_vector=version_vector,
            component_descriptor_lookup=component_descriptor_lookup,
            version_lookup=version_lookup,
            oci_client=oci_client,
            version_filter=version.is_final,
            whither_component=component,
        )
    ))

    if sub_component_release_notes:
        full_release_notes_markdown = f'{release_notes_markdown}\n{sub_component_release_notes}'
    else:
        full_release_notes_markdown = release_notes_markdown

    if (component_resources_markdown := release_notes.markdown.release_note_for_ocm_component(
        component=component,
    )):
        full_release_notes_markdown += '\n\n' + component_resources_markdown

    return release_notes_markdown, full_release_notes_markdown


def have_tag_conflicts(
    repository,
    tags,
):
    found_tags = 0
    for tag in tags:
        if github.util.tag_exists(
            repository=repository,
            tag_name=tag.removeprefix('refs/tags/'),
        ):
            logger.error(f'{tag=} exists in remote repository - aborting release')
            found_tags += 1

    if not found_tags:
        return False

    logger.error('HINT: manually bump VERSION or remove tag')
    return True


def create_release_commit(
    git_helper,
    branch: str,
    version: str,
    version_interface,
    version_path: str,
    release_commit_message_prefix: str='',
    release_commit_callback: str=None,
    release_commit_callback_image_reference: str=None,
) -> git.Commit:
    # clean repository if required
    worktree_dirty = bool(git_helper._changed_file_paths())
    if worktree_dirty:
        git_helper.repo.head.reset(working_tree=True)

    commit_message = f'Release {version}'
    if release_commit_message_prefix:
        commit_message = f'{release_commit_message_prefix} {commit_message}'

    concourse.steps.version.write_version(
        version_interface=version_interface,
        version_str=version,
        path=version_path,
    )

    if release_commit_callback:
        _invoke_callback(
            callback_script_path=release_commit_callback,
            repo_dir=git_helper.repo.working_tree_dir,
            effective_version=version,
            callback_image_reference=release_commit_callback_image_reference,
        )

    release_commit = git_helper.index_to_commit(
        message=commit_message,
    )

    # make sure working tree is clean for later git operations
    if git_helper._changed_file_paths():
        git_helper.repo.head.reset(index=True, working_tree=True)

    return release_commit


def create_and_push_bump_commit(
    git_helper: GitHelper,
    repo_dir: str,
    release_commit: git.Commit,
    merge_release_back_to_default_branch_commit: git.Commit,
    release_version: str,
    version_interface: version_trait.VersionInterface,
    version_path: str,
    repository_branch: str,
    version_operation: str,
    prerelease_suffix: str,
    publishing_policy: ReleaseCommitPublishingPolicy,
    commit_message_prefix: str='',
    next_version_callback: str=None,
):
    # clean repository if required
    worktree_dirty = bool(git_helper._changed_file_paths())

    if worktree_dirty:
        if publishing_policy is ReleaseCommitPublishingPolicy.TAG_AND_PUSH_TO_BRANCH:
            reset_to = release_commit.hexsha
        elif publishing_policy is ReleaseCommitPublishingPolicy.TAG_ONLY:
            reset_to = 'HEAD'
        elif publishing_policy is ReleaseCommitPublishingPolicy.TAG_AND_MERGE_BACK:
            reset_to = merge_release_back_to_default_branch_commit or 'HEAD'
        else:
            raise NotImplementedError

        git_helper.repo.head.reset(
            commit=reset_to,
            index=True,
            working_tree=True,
        )

    # reset to release-commit so potential changes from release-commit-callback are not
    # unintentionally reverted
    if publishing_policy is ReleaseCommitPublishingPolicy.TAG_AND_PUSH_TO_BRANCH:
        git_helper.repo.head.reset(
            commit=release_commit,
            index=True,
            working_tree=True,
        )

    # prepare next dev cycle commit
    next_version = _calculate_next_cycle_dev_version(
        release_version=release_version,
        version_operation=version_operation,
        prerelease_suffix=prerelease_suffix,
    )
    logger.info(f'{next_version=}')

    concourse.steps.version.write_version(
        version_interface=version_interface,
        version_str=next_version,
        path=version_path,
    )

    # call optional dev cycle commit callback
    if next_version_callback:
        _invoke_callback(
            callback_script_path=next_version_callback,
            repo_dir=repo_dir,
            effective_version=next_version,
        )

    # depending on publishing-policy, bump-commit should become successor of
    # either the release commit, or just be pushed to branch-head
    if publishing_policy is ReleaseCommitPublishingPolicy.TAG_AND_PUSH_TO_BRANCH:
        parent_commits = [release_commit.hexsha]
    elif publishing_policy is ReleaseCommitPublishingPolicy.TAG_ONLY:
        parent_commits = None # default to current branch head
    elif publishing_policy is ReleaseCommitPublishingPolicy.TAG_AND_MERGE_BACK:
        parent_commit = git_helper.repo.head.commit

        if parent_commit:
            parent_commits = [parent_commit]
        else:
            parent_commits = None # default to current branch head

    commit_message = f'Prepare next Development Cycle {next_version}'
    if commit_message_prefix:
        commit_message = f'{commit_message_prefix} {commit_message}'

    next_cycle_commit = git_helper.index_to_commit(
        message=commit_message,
        parent_commits=parent_commits,
    )

    git_helper.push(
        from_ref=next_cycle_commit.hexsha,
        to_ref=repository_branch,
    )


def create_and_push_tags(
    git_helper,
    tags,
    release_commit: git.Commit,
):
    for tag in tags:
        try:
            git_helper.push(
                from_ref=release_commit.hexsha,
                to_ref=tag,
            )
        except GitCommandError:
            logger.error(f'failed to push {tag=}')
            raise


def create_and_push_mergeback_commit(
    git_helper,
    tags,
    branch: str,
    merge_commit_message_prefix: str,
    release_commit: git.Commit,
):
    release_tag = tags[0]
    commit_message = f'Merge release-commit from {release_tag} into {branch}'
    if merge_commit_message_prefix:
        commit_message = f'{merge_commit_message_prefix} {commit_message}'

    git_repo = git_helper.repo

    git_repo.head.reset(
        commit=release_commit,
        index=True,
        working_tree=True,
    )

    # fetch and rebase (again, in case there was a head-update)
    upstream_commit = git_helper.fetch_head(
        f'refs/heads/{branch}'
    )
    git_helper.rebase(commit_ish=upstream_commit.hexsha)

    # update submodules (if any), to avoid local diff to be included in subsequent `git add`.
    # otherwise, upstream submodule-updates might be reverted by us.
    git_helper.submodule_update()

    # create merge commit
    git_repo.index.merge_tree(
        release_commit,
        git_repo.merge_base(upstream_commit, release_commit),
    )
    merge_commit = git_helper.index_to_commit(
        message=commit_message,
        parent_commits=(
            upstream_commit,
            release_commit,
        ),
    )

    git_helper.push(
        from_ref=merge_commit.hexsha,
        to_ref=branch
    )

    git_repo.head.reset(
        commit=merge_commit.hexsha,
        index=True,
        working_tree=True,
    ) # make sure next dev-cycle commit does not undo the merge-commit


def upload_component_descriptor_as_release_asset(
    github_release,
    component,
):
    component_descriptor = ocm.ComponentDescriptor(
        component=component,
        meta=ocm.Metadata(),
        signatures=[],
    )

    descriptor_str = yaml.dump(
        data=dataclasses.asdict(component_descriptor),
        Dumper=ocm.EnumValueYamlDumper,
    )

    normalized_component_name = component.name.replace('/', '_')
    asset_name = f'{normalized_component_name}.component-descriptor.yaml'
    try:
        github_release.upload_asset(
            content_type='application/x-yaml',
            name=asset_name,
            asset=descriptor_str.encode('utf-8'),
            label=asset_name,
        )
    except ConnectionError:
        logger.warning('Unable to attach component-descriptors to release as release-asset.')


def post_to_slack(
    release_notes_markdown,
    component: ocm.Component,
    slack_cfg_name: str,
    slack_channel: str,
):
    responses = slackclient.util.post_to_slack(
        release_notes_markdown=release_notes_markdown,
        component_name=component.name,
        release_version=component.version,
        slack_cfg_name=slack_cfg_name,
        slack_channel=slack_channel,
    )

    for response in responses:
        if response and response.get('file', None):
            uploaded_file_id = response.get('file').get('id')
            logger.info(f'uploaded {uploaded_file_id=} to slack')
        else:
            raise RuntimeError('Unable to get file id from Slack response')
    logger.info('successfully posted contents to slack')
