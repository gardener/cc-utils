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
    github_cfg_name: str,
    github_repository_owner: str,
    github_repository_name: str,
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
    repo_dir = existing_dir(repo_dir)

    github_cfg = ctx().cfg_factory().github(github_cfg_name)
    github_repo_path = f'{github_repository_owner}/{github_repository_name}'

    helper = GitHubRepositoryHelper(
        github_cfg=github_cfg,
        owner=github_repository_owner,
        name=github_repository_name,
        default_branch=repository_branch,
    )
    git_helper = GitHelper(
        repo=repo_dir,
        github_cfg=github_cfg,
        github_repo_path=github_repo_path,
    )

    if helper.tag_exists(tag_name=release_version):
        fail(
            "Cannot create tag '{t}' in preparation for release: Tag already exists".format(
                t=release_version,
            )
        )

    if rebase_before_release:
        rebase(git_helper=git_helper, upstream_ref=f'refs/heads/{repository_branch}')

    # Fetch release notes and generate markdown to catch errors early
    release_notes = fetch_release_notes(
        github_repository_owner=github_repository_owner,
        github_repository_name=github_repository_name,
        github_cfg=github_cfg,
        repo_dir=repo_dir,
        github_helper=helper,
        repository_branch=repository_branch,
    )
    release_notes_md = release_notes.to_markdown()

    # Do all the version handling upfront to catch errors early
    # Bump release version and add suffix
    next_version = version.process_version(
        version_str=release_version,
        operation=version_operation
    )
    next_version_dev = version.process_version(
        version_str=next_version,
        operation='set_prerelease',
        prerelease=prerelease_suffix
    )

    release_commit_sha = _create_and_push_release_commit(
        repo_dir=repo_dir,
        git_helper=git_helper,
        version_file_path=repository_version_file_path,
        release_version=release_version,
        target_ref=repository_branch,
        commit_msg=f'Release {release_version}',
        release_commit_callback=release_commit_callback,
    )

    helper.create_tag(
        tag_name=release_version,
        tag_message="Release " + release_version,
        repository_reference=release_commit_sha,
        author_name=author_name,
        author_email=author_email
    )
    release = helper.create_release(
        tag_name=release_version,
        body=release_notes_md,
        draft=False,
        prerelease=False
    )

    if component_descriptor_file_path:
        with open(component_descriptor_file_path) as f:
            # todo: validate descriptor
            component_descriptor_contents = f.read()
        release.upload_asset(
            content_type='application/x-yaml',
            name=product.model.COMPONENT_DESCRIPTOR_ASSET_NAME,
            asset=component_descriptor_contents,
            label=product.model.COMPONENT_DESCRIPTOR_ASSET_NAME,
        )

    # Prepare version file for next dev cycle
    helper.create_or_update_file(
        file_path=repository_version_file_path,
        file_contents=next_version_dev,
        commit_message="Prepare next dev cycle " + next_version_dev
    )

    draft_name = draft_release_name_for_version(release_version)
    draft_release = helper.draft_release_with_name(draft_name)
    if draft_release:
        verbose('cleaning up draft release {name}'.format(name=draft_release.name))
        draft_release.delete()

    if slack_cfg_name and slack_channel:
        post_to_slack(
            release_notes=release_notes,
            github_repository_name=github_repository_name,
            slack_cfg_name=slack_cfg_name,
            slack_channel=slack_channel,
            release_version=release_version,
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
