import abc
import os
import version
import subprocess
import traceback
import typing

from github3.exceptions import NotFoundError

import gci.componentmodel

import ci.util
from ci.util import (
    existing_file,
    existing_dir,
    not_empty,
    not_none,
    info,
    warning,
)
from gitutil import GitHelper
from github.util import (
    GitHubRepositoryHelper,
    GitHubRepoBranch,
)
import product.model
import product.v2
from github.release_notes.util import (
    delete_file_from_slack,
    fetch_release_notes,
    post_to_slack,
    ReleaseNotes,
)
from concourse.model.traits.release import (
    ReleaseNotesPolicy,
    ReleaseCommitPublishingPolicy,
)


class TransactionContext(object):
    def __init__(self):
        self._step_outputs = {}

    def has_output(self, step_name: str):
        return step_name in self._step_outputs.keys()

    def step_output(self, step_name: str):
        return self._step_outputs[step_name]

    def set_step_output(self, step_name: str, output):
        if self.has_output(step_name):
            raise RuntimeError(f"Context already contains output of step '{step_name}'")
        self._step_outputs[step_name] = output


class TransactionalStep(metaclass=abc.ABCMeta):
    '''Abstract base class for operations that are to be executed with transactional semantics.

    Instances represent operations which typically cause external and persistent side effects.
    Typically, a sequence of (different) steps are grouped in a `Transaction`

    Subclasses *may* overwrite the `validate` method, which performs optional checks that indicate
    whether the operation would probably fail. Those checks are intended to be run for all steps of
    a `Transaction` before actually executing it. Validation *must not* cause any persistent side
    effects to external resources.

    Subclasses *must* overwrite the `apply` method, which performs the actual payload of the step,
    typically resulting in persistent external side effects. The `apply` method *may* also return
    an object (e.g.: a `dict`) that is then made available to later steps
    when part of a `Transaction`.

    Subclasses *must* overwrite the `revert` method, which reverts any persistent external side
    effects previously created by running the step's `apply` method. This should take into account
    that the execution of the `apply` method may or may not have succeeded, failed,
    or failed partially.
    '''
    def set_context(self, context: TransactionContext):
        self._context = context

    def context(self):
        return self._context

    def validate(self):
        pass

    @abc.abstractmethod
    def apply(self):
        return None

    @abc.abstractmethod
    def revert(self):
        pass

    @abc.abstractmethod
    def name(self):
        pass


class Transaction:
    '''Represents a transaction using `TransactionalStep`s

    After creation, invoke `validate` to have the transaction validate all steps. Invoke
    `execute` to execute all steps. Both operations are done in the original step order.

    Upon encountered errors, all steps that were already executed are reverted in inverse execution
    order.
    '''
    def __init__(
        self,
        ctx: TransactionContext,
        steps: typing.Iterable[TransactionalStep],
    ):
        self._context = ci.util.check_type(ctx, TransactionContext)
        # validate type of args and set context
        for step in steps:
            ci.util.check_type(step, TransactionalStep)
            step.set_context(self._context)
        self._steps = steps

    def validate(self):
        for step in self._steps:
            info(f"Validating step '{step.name()}'")
            step.validate()

    def execute(self):
        executed_steps = list()
        for step in self._steps:
            step_name = step.name()
            info(f"Applying step '{step_name}'")
            executed_steps.append(step)
            try:
                output = step.apply()
                self._context.set_step_output(step_name, output)
            except BaseException as e:
                warning(f"An error occured while applying step '{step_name}': {e}")
                traceback.print_exc()
                # revert the changes attempted, in reverse order
                self._revert(reversed(executed_steps))
                # do not execute apply for remaining steps
                return False
        return True

    def _revert(self, steps):
        # attempt to revert each step. Raise an exception if not all reverts succeeded.
        all_reverted = True
        for step in steps:
            step_name = step.name()
            info(f"Reverting step {step_name}")
            try:
                step.revert()
            except BaseException as e:
                all_reverted = False
                warning(f"An error occured while reverting step '{step_name}': {e}")
                traceback.print_exc()
        if not all_reverted:
            raise RuntimeError("Unable to revert all steps.")


class RebaseStep(TransactionalStep):
    def __init__(self, git_helper: GitHelper, repository_branch: str):
        self.git_helper = not_none(git_helper)
        self.repository_branch = not_empty(repository_branch)

    def name(self):
        return f'Rebase against {self.repository_branch}'

    def apply(self):
        upstream_commit_sha = self.git_helper.fetch_head(
            f'refs/heads/{self.repository_branch}'
        ).hexsha
        self.git_helper.rebase(commit_ish=upstream_commit_sha)

    def revert(self):
        pass


class ReleaseCommitStep(TransactionalStep):
    def __init__(
        self,
        git_helper: GitHelper,
        repo_dir: str,
        release_version: str,
        repository_version_file_path: str,
        repository_branch: str,
        release_commit_message_prefix: str,
        publishing_policy: ReleaseCommitPublishingPolicy,
        release_commit_callback: str=None,
    ):
        self.git_helper = not_none(git_helper)
        self.repository_branch = not_empty(repository_branch)
        repo_dir_absolute = os.path.abspath(not_empty(repo_dir))
        self.repo_dir = repo_dir_absolute
        self.release_version = not_empty(release_version)
        self.repository_version_file_path = os.path.join(
            repo_dir_absolute,
            repository_version_file_path,
        )
        self.release_commit_message_prefix = release_commit_message_prefix
        self.publishing_policy = publishing_policy

        if release_commit_callback:
            self.release_commit_callback = os.path.join(
                repo_dir_absolute,
                release_commit_callback,
            )
        else:
            self.release_commit_callback = None

        self.head_commit = None # stored while applying - used for revert

    def _release_commit_message(self, version: str, release_commit_message_prefix: str=''):
        message = f'Release {version}'
        if release_commit_message_prefix:
            return f'{release_commit_message_prefix} {message}'
        else:
            return message

    def name(self):
        return 'Create Release Commit'

    def validate(self):
        existing_dir(self.repo_dir)
        version.parse_to_semver(self.release_version)
        if(self.release_commit_callback):
            existing_file(self.release_commit_callback)

        existing_file(self.repository_version_file_path)

    def apply(self):
        # clean repository if required
        worktree_dirty = bool(self.git_helper._changed_file_paths())
        if worktree_dirty:
            self.git_helper.repo.head.reset(working_tree=True)

        # store head-commit (type: git.Commit)
        self.head_commit = self.git_helper.repo.head.commit
        self.context().head_commit = self.head_commit # pass to other steps

        # prepare release commit
        with open(self.repository_version_file_path, 'w') as f:
            f.write(self.release_version)

        # call optional release commit callback
        if self.release_commit_callback:
            _invoke_callback(
                callback_script_path=self.release_commit_callback,
                repo_dir=self.repo_dir,
                effective_version=self.release_version,
            )

        release_commit = self.git_helper.index_to_commit(
            message=self._release_commit_message(
                self.release_version,
                self.release_commit_message_prefix
            ),
        )

        self.context().release_commit = release_commit # pass to other steps

        if self.publishing_policy is ReleaseCommitPublishingPolicy.TAG_AND_PUSH_TO_BRANCH:
            to_ref = self.repository_branch
        elif self.publishing_policy is ReleaseCommitPublishingPolicy.TAG_ONLY:
            to_ref = f'refs/tags/{self.release_version}'
        else:
            raise NotImplementedError

        # Push commit to remote
        self.git_helper.push(
            from_ref=release_commit.hexsha,
            to_ref=to_ref
        )

        return {
            'release_commit_sha1': release_commit.hexsha,
        }

    def revert(self):
        if not self.context().has_output(self.name()):
            # push unsuccessful, nothing to do
            return
        else:
            output = self.context().step_output(self.name())
            # create revert commit for the release commit and push it, but first
            # clean repository if required
            worktree_dirty = bool(self.git_helper._changed_file_paths())
            if worktree_dirty:
                self.git_helper.repo.head.reset(working_tree=True)

            self.git_helper.repo.git.revert(
                output['release_commit_sha1'],
                no_edit=True,
                no_commit=True,
            )
            release_revert_commit = _add_all_and_create_commit(
                git_helper=self.git_helper,
                message=f"Revert '{self._release_commit_message(self.release_version)}'"
            )
            self.git_helper.push(
                from_ref=release_revert_commit.hexsha,
                to_ref=self.repository_branch,
            )


class NextDevCycleCommitStep(TransactionalStep):
    def __init__(
        self,
        git_helper: GitHelper,
        repo_dir: str,
        release_version: str,
        repository_version_file_path: str,
        repository_branch: str,
        version_operation: str,
        prerelease_suffix: str,
        publishing_policy: ReleaseCommitPublishingPolicy,
        next_cycle_commit_message_prefix: str=None,
        next_version_callback: str=None,
    ):
        self.git_helper = not_none(git_helper)
        self.repository_branch = not_empty(repository_branch)
        repo_dir_absolute = os.path.abspath(not_empty(repo_dir))
        self.repo_dir = repo_dir_absolute
        self.release_version = not_empty(release_version)
        self.version_operation = not_empty(version_operation)
        self.prerelease_suffix = not_empty(prerelease_suffix)
        self.publishing_policy = publishing_policy
        self.next_cycle_commit_message_prefix = next_cycle_commit_message_prefix

        self.repository_version_file_path = os.path.join(
            repo_dir_absolute,
            repository_version_file_path,
        )

        if next_version_callback:
            self.next_version_callback = os.path.join(
                repo_dir_absolute,
                next_version_callback,
            )
        else:
            self.next_version_callback = None

    def _next_dev_cycle_commit_message(self, version: str, message_prefix: str):
        message = f'Prepare Next Dev Cycle {version}'
        if message_prefix:
            message = f'{message_prefix} {message}'
        return message

    def name(self):
        return 'Create next development cycle commit'

    def validate(self):
        existing_dir(self.repo_dir)
        version.parse_to_semver(self.release_version)
        if self.next_version_callback:
            existing_file(self.next_version_callback)

        existing_file(self.repository_version_file_path)

        # perform version ops once to validate args
        _calculate_next_cycle_dev_version(
            release_version=self.release_version,
            version_operation=self.version_operation,
            prerelease_suffix=self.prerelease_suffix,
        )

    def apply(self):
        # clean repository if required
        worktree_dirty = bool(self.git_helper._changed_file_paths())
        if worktree_dirty:
            if self.publishing_policy is ReleaseCommitPublishingPolicy.TAG_AND_PUSH_TO_BRANCH:
                reset_to = self.context().release_commit
            elif self.publishing_policy is ReleaseCommitPublishingPolicy.TAG_ONLY:
                reset_to = 'HEAD'
            else:
                raise NotImplementedError

            self.git_helper.repo.head.reset(
                commit=reset_to,
                index=True,
                working_tree=True,
            )

        # prepare next dev cycle commit
        next_version = _calculate_next_cycle_dev_version(
            release_version=self.release_version,
            version_operation=self.version_operation,
            prerelease_suffix=self.prerelease_suffix,
        )
        info(f'next version: {next_version}')

        with open(self.repository_version_file_path, 'w') as f:
            f.write(next_version)

        # call optional dev cycle commit callback
        if self.next_version_callback:
            _invoke_callback(
                callback_script_path=self.next_version_callback,
                repo_dir=self.repo_dir,
                effective_version=next_version,
            )

        # depending on publishing-policy, bump-commit should become successor of
        # either the release commit, or just be pushed to branch-head
        if self.publishing_policy is ReleaseCommitPublishingPolicy.TAG_AND_PUSH_TO_BRANCH:
            parent_commits = [self.context().release_commit]
        elif self.publishing_policy is ReleaseCommitPublishingPolicy.TAG_ONLY:
            parent_commits = None # default to current branch head

        next_cycle_commit = self.git_helper.index_to_commit(
            message=self._next_dev_cycle_commit_message(
                version=next_version,
                message_prefix=self.next_cycle_commit_message_prefix,
            ),
            parent_commits=parent_commits,
        )

        # Push commit to remote
        self.git_helper.push(
            from_ref=next_cycle_commit.hexsha,
            to_ref=self.repository_branch,
        )
        return {
            'next cycle commit sha': next_cycle_commit.hexsha,
        }

    def revert(self):
        if not self.context().has_output(self.name()):
            # push unsuccessful, nothing to do
            return
        else:
            output = self.context().step_output(self.name())
            # create revert commit for the next dev cycle commit and push it, but first
            # clean repository if required
            worktree_dirty = bool(self.git_helper._changed_file_paths())
            if worktree_dirty:
                self.git_helper.repo.head.reset(working_tree=True)

            next_cycle_dev_version = _calculate_next_cycle_dev_version(
                release_version=self.release_version,
                version_operation=self.version_operation,
                prerelease_suffix=self.prerelease_suffix,
            )
            commit_message = self._next_dev_cycle_commit_message(
                version=next_cycle_dev_version,
                message_prefix=self.self.next_cycle_commit_message_prefix,
            )
            self.git_helper.repo.git.revert(
                output['next cycle commit sha'],
                no_edit=True,
                no_commit=True,
            )
            next_cycle_revert_commit = _add_all_and_create_commit(
                git_helper=self.git_helper,
                message=f"Revert '{commit_message}'"
            )
            self.git_helper.push(
                from_ref=next_cycle_revert_commit.hexsha,
                to_ref=self.repository_branch,
            )


class GitHubReleaseStep(TransactionalStep):
    def __init__(
        self,
        github_helper: GitHubRepositoryHelper,
        githubrepobranch: GitHubRepoBranch,
        repo_dir: str,
        release_version: str,
        component_descriptor_file_path:str,
        component_descriptor_v2_path:str = None,
    ):
        self.github_helper = not_none(github_helper)
        self.githubrepobranch = githubrepobranch
        self.release_version = not_empty(release_version)
        self.component_descriptor_file_path = os.path.abspath(
            not_empty(component_descriptor_file_path)
        )

        repo_dir_absolute = os.path.abspath(not_empty(repo_dir))
        self.repo_dir = repo_dir_absolute
        self.component_descriptor_v2_path = component_descriptor_v2_path

    def name(self):
        return "Create Release"

    def validate(self):
        version.parse_to_semver(self.release_version)
        # if a tag with the given release version exists, we cannot create another release
        # pointing to it
        if self.github_helper.tag_exists(tag_name=self.release_version):
            raise RuntimeError(
                f"Cannot create tag '{self.release_version}' for release: Tag already exists"
            )
        existing_file(self.component_descriptor_file_path)
        with open(self.component_descriptor_file_path) as f:
            # TODO: Proper validation
            not_empty(f.read().strip())

    def apply(
        self,
    ):
        release_commit_step_output = self.context().step_output('Create Release Commit')
        release_commit_sha = release_commit_step_output['release_commit_sha1']
        # Create GitHub-release
        release = self.github_helper.repository.create_release(
            tag_name=self.release_version,
            target_commitish=release_commit_sha,
            body=None,
            draft=False,
            prerelease=False,
            name=self.release_version,
        )

        # Upload component descriptor to GitHub-release if one has been calculated
        with open(self.component_descriptor_file_path) as f:
            component_descriptor_contents = f.read()
            release.upload_asset(
                content_type='application/x-yaml',
                name=product.model.COMPONENT_DESCRIPTOR_ASSET_NAME,
                asset=component_descriptor_contents,
                label=product.model.COMPONENT_DESCRIPTOR_ASSET_NAME,
            )
        if self.component_descriptor_v2_path:
            cdv2_dict = ci.util.parse_yaml_file(self.component_descriptor_v2_path)
            component_descriptor_v2 = gci.componentmodel.ComponentDescriptor.from_dict(
                component_descriptor_dict=cdv2_dict,
            )
            info('publishing component-descriptor v2')
            product.v2.upload_component_descriptor_v2_to_oci_registry(
                component_descriptor_v2=component_descriptor_v2,
            )
            info('resolving / importing dependencies')
            try:
                product.v2.resolve_dependencies(component=component_descriptor_v2.component)
            except:
                import traceback
                traceback.print_exc()

    def revert(self):
        # Fetch release
        try:
            release = self.github_helper.repository.release_from_tag(self.release_version)
        except NotFoundError:
            release = None
        if release:
            info(f"Deleting Release {self.release_version}")
            if not release.delete():
                raise RuntimeError("Release could not be deleted")
        try:
            tag = self.github_helper.repository.ref(f"tags/{self.release_version}")
        except NotFoundError:
            # Ref wasn't created
            return
        if not tag.delete():
            raise RuntimeError("Tag could not be deleted")


class PublishReleaseNotesStep(TransactionalStep):
    def name(self):
        return "Publish Release Notes"

    def __init__(
        self,
        githubrepobranch: GitHubRepoBranch,
        github_helper: GitHubRepositoryHelper,
        release_version: str,
        repo_dir: str,
    ):
        self.githubrepobranch = not_none(githubrepobranch)
        self.github_helper = not_none(github_helper)
        self.release_version = not_empty(release_version)
        self.repo_dir = os.path.abspath(not_empty(repo_dir))

    def validate(self):
        version.parse_to_semver(self.release_version)
        existing_dir(self.repo_dir)

        # check whether a release with the given version exists
        try:
            self.github_helper.repository.release_from_tag(self.release_version)
        except NotFoundError:
            raise RuntimeError(f'No release with tag {self.release_version} found')

    def apply(self):
        release_notes = fetch_release_notes(
            github_repository_owner=self.githubrepobranch.repo_owner(),
            github_repository_name=self.githubrepobranch.repo_name(),
            github_cfg=self.githubrepobranch.github_config(),
            repo_dir=self.repo_dir,
            github_helper=self.github_helper,
            repository_branch=self.githubrepobranch.branch(),
        )
        release_notes_md = release_notes.to_markdown()
        self.github_helper.update_release_notes(
            tag_name=self.release_version,
            body=release_notes_md,
        )
        return {
            'release notes': release_notes,
            'release notes markdown': release_notes_md,
        }

    def revert(self):
        if not self.context().has_output(self.name()):
            # Updating release notes was unsuccessful, nothing to do
            return
        # purge release notes
        self.github_helper.update_release_notes(
            tag_name=self.release_version,
            body='',
        )


class TryCleanupDraftReleasesStep(TransactionalStep):
    def name(self):
        return "Try to Cleanup Draft Releases"

    def __init__(
        self,
        github_helper: GitHubRepositoryHelper,
    ):
        self.github_helper = not_none(github_helper)

    def validate(self):
        # nothing to validate
        pass

    def apply(self):
        for release, deletion_successful in self.github_helper.delete_outdated_draft_releases():
            if deletion_successful:
                ci.util.info(f"Deleted release '{release.name}'")
            else:
                ci.util.warning(f"Could not delete release '{release.name}'")
        return

    def revert(self):
        # nothing to revert
        pass


class PostSlackReleaseStep(TransactionalStep):
    def name(self):
        return "Post Slack Release"

    def __init__(
        self,
        slack_cfg_name: str,
        slack_channel: str,
        release_version: str,
        release_notes: ReleaseNotes,
        githubrepobranch: GitHubRepoBranch,
    ):
        self.slack_cfg_name = not_empty(slack_cfg_name)
        self.slack_channel = not_empty(slack_channel)
        self.release_version = not_empty(release_version)
        self.githubrepobranch = not_none(githubrepobranch)
        self.release_notes = not_none(release_notes)

    def validate(self):
        version.parse_to_semver(self.release_version)

    def apply(self):
        responses = post_to_slack(
            release_notes=self.release_notes,
            github_repository_name=self.githubrepobranch.github_repo_path(),
            slack_cfg_name=self.slack_cfg_name,
            slack_channel=self.slack_channel,
            release_version=self.release_version,
        )

        for response in responses:
            if response and response.get('file', None):
                uploaded_file_id = response.get('file').get('id')
                info(f'uploaded file id {uploaded_file_id} to slack')
            else:
                raise RuntimeError("Unable to get file id from Slack response")
        info('successfully posted contents to slack')

    def revert(self):
        if not self.context().has_output(self.name()):
            # Posting the release notes was unsuccessful, nothing to revert
            return
        uploaded_file_id = self.context().step_output(self.name()).get('uploaded file id')
        delete_file_from_slack(
            slack_cfg_name=self.slack_cfg_name,
            file_id=uploaded_file_id,
        )


def _invoke_callback(
    callback_script_path: str,
    repo_dir: str,
    effective_version: str,
):
    '''This invokes the callback script with the REPO_DIR and EFFECTIVE_VERSION
    env variable set to the given values.
    '''
    callback_env = os.environ.copy()
    callback_env['REPO_DIR'] = repo_dir
    callback_env['EFFECTIVE_VERSION'] = effective_version
    subprocess.run(
        [callback_script_path],
        check=True,
        env=callback_env,
    )


def _add_all_and_create_commit(git_helper: GitHelper, message: str):
    commit = git_helper.index_to_commit(
        message=message,
    )
    git_helper.repo.head.reset(
        commit=commit,
        working_tree=True,
    )
    return commit


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


def release_and_prepare_next_dev_cycle(
    githubrepobranch: GitHubRepoBranch,
    release_commit_publishing_policy: str,
    release_notes_policy: str,
    release_version: str,
    repo_dir: str,
    repository_version_file_path: str,
    component_descriptor_file_path: str,
    author_email: str="gardener.ci.user@gmail.com",
    author_name: str="gardener-ci",
    component_descriptor_v2_path: str=None,
    next_cycle_commit_message_prefix: str=None,
    next_version_callback: str=None,
    prerelease_suffix: str="dev",
    rebase_before_release: bool=False,
    release_commit_callback: str=None,
    release_commit_message_prefix: str=None,
    slack_cfg_name: str=None,
    slack_channel: str=None,
    version_operation: str="bump_minor",
):
    transaction_ctx = TransactionContext() # shared between all steps/trxs

    release_notes_policy = ReleaseNotesPolicy(release_notes_policy)
    release_commit_publishing_policy = ReleaseCommitPublishingPolicy(
        release_commit_publishing_policy
    )
    github_helper = GitHubRepositoryHelper.from_githubrepobranch(githubrepobranch)
    git_helper = GitHelper.from_githubrepobranch(
        githubrepobranch=githubrepobranch,
        repo_path=repo_dir,
    )

    step_list = []

    if rebase_before_release:
        rebase_step = RebaseStep(
            git_helper=git_helper,
            repository_branch=githubrepobranch.branch(),
        )
        step_list.append(rebase_step)

    release_commit_step = ReleaseCommitStep(
        git_helper=git_helper,
        repo_dir=repo_dir,
        release_version=release_version,
        repository_version_file_path=repository_version_file_path,
        repository_branch=githubrepobranch.branch(),
        release_commit_message_prefix=release_commit_message_prefix,
        release_commit_callback=release_commit_callback,
        publishing_policy=release_commit_publishing_policy,
    )
    step_list.append(release_commit_step)

    if version_operation != version.NOOP:
        next_cycle_commit_step = NextDevCycleCommitStep(
            git_helper=git_helper,
            repo_dir=repo_dir,
            release_version=release_version,
            repository_version_file_path=repository_version_file_path,
            repository_branch=githubrepobranch.branch(),
            version_operation=version_operation,
            prerelease_suffix=prerelease_suffix,
            next_version_callback=next_version_callback,
            publishing_policy=release_commit_publishing_policy,
            next_cycle_commit_message_prefix=next_cycle_commit_message_prefix,
        )
        step_list.append(next_cycle_commit_step)

    github_release_step = GitHubReleaseStep(
        github_helper=github_helper,
        githubrepobranch=githubrepobranch,
        repo_dir=repo_dir,
        release_version=release_version,
        component_descriptor_file_path=component_descriptor_file_path,
        component_descriptor_v2_path=component_descriptor_v2_path,
    )
    step_list.append(github_release_step)

    release_transaction = Transaction(
        ctx=transaction_ctx,
        steps=step_list
    )

    release_transaction.validate()
    if not release_transaction.execute():
        raise RuntimeError('An error occurred while creating the Release.')

    publish_release_notes_step = PublishReleaseNotesStep(
        githubrepobranch=githubrepobranch,
        github_helper=github_helper,
        release_version=release_version,
        repo_dir=repo_dir,
    )

    cleanup_draft_releases_step = TryCleanupDraftReleasesStep(
        github_helper=github_helper,
    )

    cleanup_draft_releases_transaction = Transaction(
        ctx=transaction_ctx,
        steps=(cleanup_draft_releases_step,),
    )

    if not cleanup_draft_releases_transaction.execute():
        ci.util.warning('An error occured while cleaning up draft releases')

    if release_notes_policy == ReleaseNotesPolicy.DISABLED:
        return info('release notes were disabled - skipping')
    elif release_notes_policy == ReleaseNotesPolicy.DEFAULT:
        pass
    else:
        raise NotImplementedError(release_notes_policy)

    release_notes_transaction = Transaction(
        ctx=transaction_ctx,
        steps=(publish_release_notes_step,),
    )
    release_notes_transaction.validate()
    if not release_notes_transaction.execute():
        raise RuntimeError('An error occurred while publishing the release notes.')

    if slack_cfg_name and slack_channel:
        release_notes = transaction_ctx.step_output(
            publish_release_notes_step.name()
            ).get('release notes')

        post_to_slack_step = PostSlackReleaseStep(
            slack_cfg_name=slack_cfg_name,
            slack_channel=slack_channel,
            release_version=release_version,
            release_notes=release_notes,
            githubrepobranch=githubrepobranch,
        )
        slack_transaction = Transaction(
            ctx=transaction_ctx,
            steps=(post_to_slack_step,),
        )
        slack_transaction.validate()
        if not slack_transaction.execute():
            raise RuntimeError('An error occurred while posting the release notes to Slack.')
