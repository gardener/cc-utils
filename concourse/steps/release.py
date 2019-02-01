import os
import version
import pathlib
import subprocess

from util import (
    ctx,
    existing_file,
    existing_dir,
    fail,
    verbose,
)
from gitutil import GitHelper
from github.util import (
    GitHubRepositoryHelper,
)
import product.model
from github.release_notes.util import (
    fetch_release_notes,
    post_to_slack,
    github_repo_path,
    draft_release_name_for_version,
)


def release_and_prepare_next_dev_cycle(
    github_helper: GitHubRepositoryHelper,
    git_helper: GitHelper,
    repository_branch: str,
    repository_version_file_path: str,
    release_version: str,
    repo_dir:str,
    release_commit_callback: str=None,
    version_operation: str="bump_minor",
    prerelease_suffix: str="dev",
    author_name: str="gardener-ci",
    author_email: str="gardener.ci.user@gmail.com",
    component_descriptor_file_path: str=None,
    slack_cfg_name: str=None,
    slack_channel: str=None,
    rebase_before_release: bool=False,
):
    github_repository_name = github_helper.repository_name
    # perform validation asap
    next_version, next_version_dev, release_notes_md = prepare_release(
        github_helper=github_helper,
        git_helper=git_helper,
        repository_branch=repository_branch,
        release_version=release_version,
        version_operation=version_operation,
        prerelease_suffix=prerelease_suffix,
    )

    if rebase_before_release:
        git_helper.rebase_on_remote_ref(remote_ref=f'refs/heads/{repository_branch}')

    create_release_on_github(
        git_helper=git_helper,
        release_version=next_version,
        release_notes=release_notes_md,
        release_commit_callback=release_commit_callback,
        repo_dir=repo_dir,
        repository_branch=repository_branch,
        repository_version_file_path=repository_version_file_path,
        next_version=next_version_dev,
        author_name=author_name,
        author_email=author_email,
        component_descriptor_file_path=component_descriptor_file_path,
    )

    prepare_next_dev_cycle(
        github_helper=github_helper,
        repository_version_file_path=repository_version_file_path,
        next_dev_version=next_version_dev,
    )

    cleanup_draft_releases(next_version)

    if slack_cfg_name and slack_channel:
        post_to_slack(
            release_notes=release_notes,
            github_repository_name=github_repository_name,
            slack_cfg_name=slack_cfg_name,
            slack_channel=slack_channel,
            release_version=next_version,
        )


def _create_and_push_release_commit(
        repo_dir: str,
        git_helper,
        version_file_path: str,
        release_version: str,
        target_ref: str,
        commit_msg: str,
        release_commit_callback: str=None,
):
    if release_commit_callback:
        release_commit_callback = os.path.join(repo_dir, release_commit_callback)
        existing_file(release_commit_callback)

    def invoke_release_callback():
        if not release_commit_callback:
            return # early exit if optional callback is absent

        callback_env = os.environ.copy()
        callback_env['REPO_DIR'] = repo_dir
        callback_env['EFFECTIVE_VERSION'] = release_version

        subprocess.run(
            [release_commit_callback],
            check=True,
            env=callback_env,
        )

    try:
        # clean repository if required
        worktree_dirty = bool(git_helper._changed_file_paths())
        if worktree_dirty:
            git_helper._stash_changes()

        # update version file
        version_file = pathlib.Path(repo_dir, version_file_path)
        version_file.write_text(release_version)

        # call optional release commit callback
        invoke_release_callback()

        release_commit = git_helper.index_to_commit(message=commit_msg)

        # forward head to new commit
        git_helper.repo.head.set_commit(release_commit.hexsha)

        git_helper.push(
            from_ref=release_commit.hexsha,
            to_ref=target_ref,
            use_ssh=True,
        )
    finally:
        if worktree_dirty and git_helper._has_stash():
            git_helper._pop_stash()

    return release_commit.hexsha


def prepare_release(
    github_helper: GitHubRepositoryHelper,
    git_helper: GitHelper,
    repository_branch: str,
    release_version: str,
    version_operation: str,
    prerelease_suffix: str,
):
    # Do all the validation, release note processing and
    # version handling upfront to catch errors early
    if github_helper.tag_exists(tag_name=release_version):
        fail(
            f"Cannot create tag '{release_version}' in preparation for release: Tag already exists"
        )

    release_notes = fetch_release_notes(
        github_repository_owner=github_repository_owner,
        github_repository_name=github_repository_name,
        github_cfg=github_cfg,
        repo_dir=repo_dir,
        github_helper=helper,
        repository_branch=repository_branch,
    )
    release_notes_md = release_notes.to_markdown()

    next_version = version.process_version(
        version_str=release_version,
        operation=version_operation
    )

    next_version_dev = version.process_version(
        version_str=next_version,
        operation='set_prerelease',
        prerelease=prerelease_suffix
    )

    if component_descriptor_file_path:
        with open(component_descriptor_file_path) as f:
            # TODO: validate descriptor
            component_descriptor_contents = f.read()

    return (next_version, next_version_dev, release_notes_md)


def create_release_on_github(
    git_helper: GitHelper,
    github_helper: GitHubRepositoryHelper,
    release_version,
    release_notes,
    release_commit_callback,
    repo_dir: str,
    repository_branch: str,
    repository_version_file_path: str,
    next_version: str,
    author_name: str,
    author_email: str,
    component_descriptor_file_path,
):
    release_commit_sha = _create_and_push_release_commit(
        repo_dir=repo_dir,
        git_helper=git_helper,
        version_file_path=repository_version_file_path,
        release_version=release_version,
        target_ref=repository_branch,
        commit_msg=f'Release {release_version}',
        release_commit_callback=release_commit_callback,
    )

    github_helper.create_tag(
        tag_name=release_version,
        tag_message="Release " + release_version,
        repository_reference=release_commit_sha,
        author_name=author_name,
        author_email=author_email
    )
    release = github_helper.create_release(
        tag_name=release_version,
        body=release_notes,
        draft=False,
        prerelease=False
    )

    if component_descriptor_file_path:
        with open(component_descriptor_file_path) as f:
            # TODO: Do not duplicate in 'prepare_release'
            component_descriptor_contents = f.read()
        release.upload_asset(
            content_type='application/x-yaml',
            name=product.model.COMPONENT_DESCRIPTOR_ASSET_NAME,
            asset=component_descriptor_contents,
            label=product.model.COMPONENT_DESCRIPTOR_ASSET_NAME,
        )


def prepare_next_dev_cycle(
    github_helper: GitHubRepositoryHelper,
    repository_version_file_path: str,
    next_dev_version: str,
):
    github_helper.create_or_update_file(
        file_path=repository_version_file_path,
        file_contents=next_dev_version,
        commit_message="Prepare next dev cycle " + next_dev_version
    )


def cleanup_draft_releases(
    release_version: str,
):
    # TODO: Cleanup _ALL_ draft releases, similar to our upgrade-PR-handling
    draft_name = draft_release_name_for_version(release_version)
    draft_release = github_helper.draft_release_with_name(draft_name)
    if draft_release:
        info(f'cleaning up draft release {draft_release.name}')
        draft_release.delete()
