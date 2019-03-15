import abc
import os
import version
import semver
import subprocess
import traceback

from github3.exceptions import NotFoundError

from util import (
    ctx,
    existing_file,
    existing_dir,
    not_empty,
    not_none,
    fail,
    verbose,
    info,
    warning,
)
from gitutil import GitHelper
from github.util import (
    GitHubRepositoryHelper,
    GitHubRepoBranch,
)
import product.model
from github.release_notes.util import (
    fetch_release_notes,
    post_to_slack,
    delete_file_from_slack,
    github_repo_path,
    draft_release_name_for_version,
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


class TransactionalStep(object, metaclass=abc.ABCMeta):
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


class Transaction(object):
    '''This class represents a transaction using `TransactionalStep`s

    Provides aptly named methods to `validate` a series of `TransactionalStep`s and `execute`
    it atomically, performing the necessary undo actions should an error occur.

    `TransactionalStep`s are provided with access to a shared `TransactionContext` instance
    to store and retrieve (by step name) information with greater scope than a single step.
    '''
    def __init__(
        self,
        *steps: TransactionalStep,
    ):
        # create context object for this transaction
        self.context = TransactionContext()
        # validate type of args and set context
        for step in steps:
            if not isinstance(step, TransactionalStep):
                raise TypeError('Transactions may only contain instances of TransactionalStep')
            step.set_context(self.context)
        self._steps = steps

    def validate(self):
        for step in self._steps:
            info(f"Validating step '{step.name()}'")
            step.validate()

    def execute(self):
        executed_steps = list()
        for step in self._steps:
            step_name = step.name()
            # attempt to execute the step
            info(f"Applying step '{step_name}'")
            executed_steps.append(step)
            try:
                output = step.apply()
                self.context.set_step_output(step_name, output)
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


class ReleaseCommitsStep(TransactionalStep):
    def __init__(
        self,
        git_helper: GitHelper,
        repo_dir: str,
        release_version: str,
        repository_version_file_path: str,
        repository_branch: str,
        rebase_before_release: bool,
        version_operation: str,
        prerelease_suffix: str,
        release_commit_callback: str=None,
        next_version_callback: str=None,
    ):
        self.git_helper = not_none(git_helper)
        self.repository_branch = not_empty(repository_branch)
        self.release_version = not_empty(release_version)
        self.rebase_before_release = rebase_before_release

        self.version_operation = not_empty(version_operation)
        self.prerelease_suffix = not_empty(prerelease_suffix)

        repo_dir_absolute = os.path.abspath(not_empty(repo_dir))
        self.repo_dir = repo_dir_absolute
        self.repository_version_file_path = os.path.join(
            repo_dir_absolute,
            repository_version_file_path,
        )

        if release_commit_callback:
            self.release_commit_callback = os.path.join(
                repo_dir_absolute,
                release_commit_callback,
            )
        else:
            self.release_commit_callback = None

        if next_version_callback:
            self.next_version_callback = os.path.join(
                repo_dir_absolute,
                next_version_callback,
            )
        else:
            self.next_version_callback = None

    def name(self):
        return "Create Release Commits"

    def validate(self):
        existing_dir(self.repo_dir)
        semver.parse(self.release_version)

        if(self.release_commit_callback):
            existing_file(self.release_commit_callback)
        if self.next_version_callback:
            existing_file(self.next_version_callback)

        existing_file(self.repository_version_file_path)

        # perform version ops once to validate args
        self._calculate_next_cycle_dev_version(
            release_version=self.release_version,
            version_operation=self.version_operation,
            prerelease_suffix=self.prerelease_suffix,
        )

    def _release_commit_message(self, version: str):
        return f'Release {version}'

    def _next_dev_cycle_commit_message(self, version: str):
        return f'Prepare next dev cycle {version}'

    def apply(self):
        if self.rebase_before_release:
            upstream_commit_sha = self.git_helper.fetch_head(
                f'refs/heads/{self.repository_branch}'
            ).hexsha
            self.git_helper.rebase(commit_ish=upstream_commit_sha)

        # clean repository if required
        worktree_dirty = bool(self.git_helper._changed_file_paths())
        if worktree_dirty:
            self.git_helper.repo.head.reset(working_tree=True)

        # first, prepare release commit
        with open(self.repository_version_file_path, 'w') as f:
            f.write(self.release_version)

        # call optional release commit callback
        if self.release_commit_callback:
            self._invoke_callback(
                callback_script_path=self.release_commit_callback,
                repo_dir=self.repo_dir,
                effective_version=self.release_version,
            )

        release_commit = self._add_all_and_create_commit(
            message=self._release_commit_message(self.release_version),
        )

        # second, prepare next dev cycle commit
        next_cycle_dev_version = self._calculate_next_cycle_dev_version(
            release_version=self.release_version,
            version_operation=self.version_operation,
            prerelease_suffix=self.prerelease_suffix,
        )
        with open(self.repository_version_file_path, 'w') as f:
            f.write(next_cycle_dev_version)

        # call optional dev cycle commit callback
        if self.next_version_callback:
            self._invoke_callback(
                callback_script_path=self.next_version_callback,
                repo_dir=self.repo_dir,
                effective_version=next_cycle_dev_version,
            )

        next_cycle_commit = self._add_all_and_create_commit(
            message=self._next_dev_cycle_commit_message(next_cycle_dev_version)
        )
        next_cycle_commit_sha = next_cycle_commit.hexsha

        # Push commits to remote
        self.git_helper.push(
            from_ref=next_cycle_commit_sha,
            to_ref=self.repository_branch,
            use_ssh=True,
        )
        return {
            'release commit sha': release_commit.hexsha,
            'next cycle commit sha': next_cycle_commit_sha,
            }

    def revert(self):
        if not self.context().has_output(self.name()):
            # push unsuccessful, nothing to do
            return
        else:
            output = self.context().step_output(self.name())
            # create revert commits for the release commits and push them, but first
            # clean repository if required
            worktree_dirty = bool(self.git_helper._changed_file_paths())
            if worktree_dirty:
                self.git_helper.repo.head.reset(working_tree=True)

            # revert in reverse order. We need to create the commits using our githelper
            # otherwise git will complain about missing author information
            next_cycle_dev_version = self._calculate_next_cycle_dev_version(
                release_version=self.release_version,
                version_operation=self.version_operation,
                prerelease_suffix=self.prerelease_suffix,
            )
            self.git_helper.repo.git.revert(
                output['next cycle commit sha'],
                no_edit=True,
                no_commit=True,
            )
            self._add_all_and_create_commit(
                message=f"Revert '{self._next_dev_cycle_commit_message(next_cycle_dev_version)}'"
            )
            self.git_helper.repo.git.revert(
                output['release commit sha'],
                no_edit=True,
                no_commit=True,
            )
            release_revert_commit = self._add_all_and_create_commit(
                message=f"Revert '{self._release_commit_message(self.release_version)}'"
            )
            self.git_helper.push(
                from_ref=release_revert_commit.hexsha,
                to_ref=self.repository_branch,
                use_ssh=True,
            )

    def _invoke_callback(
        self,
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

    def _add_all_and_create_commit(self, message: str):
        commit = self.git_helper.index_to_commit(
            message=message,
        )
        self.git_helper.repo.head.reset(
            commit=commit,
            working_tree=True,
        )
        return commit

    def _calculate_next_cycle_dev_version(
        self,
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


class GitHubReleaseStep(TransactionalStep):
    def __init__(
        self,
        github_helper: GitHubRepositoryHelper,
        githubrepobranch: GitHubRepoBranch,
        repo_dir: str,
        release_version: str,
        component_descriptor_file_path:str = None,
    ):
        self.github_helper = not_none(github_helper)
        self.githubrepobranch = githubrepobranch
        self.release_version = not_empty(release_version)

        repo_dir_absolute = os.path.abspath(not_empty(repo_dir))
        self.repo_dir = repo_dir_absolute
        if component_descriptor_file_path:
            self.component_descriptor_file_path = os.path.abspath(
                not_empty(component_descriptor_file_path)
            )
        else:
            self.component_descriptor_file_path = None

    def name(self):
        return "Create Release"

    def validate(self):
        semver.parse(self.release_version)
        # if a tag with the given release version exists, we cannot create another release
        # pointing to it
        if self.github_helper.tag_exists(tag_name=self.release_version):
            raise RuntimeError(
                f"Cannot create tag '{self.release_version}' for release: Tag already exists"
            )
        if self.component_descriptor_file_path:
            existing_file(self.component_descriptor_file_path)
            with open(self.component_descriptor_file_path) as f:
                # TODO: Proper validation
                not_empty(f.read().strip())

    def apply(
        self,
    ):
        release_commit_step_output = self.context().step_output('Create Release Commits')
        release_commit_sha = release_commit_step_output['release commit sha']
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
        if self.component_descriptor_file_path:
            with open(self.component_descriptor_file_path) as f:
                component_descriptor_contents = f.read()
                release.upload_asset(
                    content_type='application/x-yaml',
                    name=product.model.COMPONENT_DESCRIPTOR_ASSET_NAME,
                    asset=component_descriptor_contents,
                    label=product.model.COMPONENT_DESCRIPTOR_ASSET_NAME,
                )

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
        semver.parse(self.release_version)
        existing_dir(self.repo_dir)

        # check whether a release with the given version exists
        try:
            release = self.github_helper.repository.release_from_tag(self.release_version)
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


class CleanupDraftReleaseStep(TransactionalStep):
    def name(self):
        return "Cleanup Draft Release"

    def __init__(
        self,
        github_helper: GitHubRepositoryHelper,
        release_version: str,
    ):
        self.github_helper = not_none(github_helper)
        self.release_version = not_empty(release_version)

    def validate(self):
        semver.parse(self.release_version)

    def apply(self):
        draft_name = draft_release_name_for_version(self.release_version)
        draft_release = self.github_helper.draft_release_with_name(draft_name)
        if draft_release:
            # store output data for possible later revert
            output = {
                'release name': draft_release.name,
                'release body': draft_release.body,
            }
            # TODO: clean up ALL previously made draft-releases (just in case)
            draft_release.delete()
            return output

    def revert(self):
        if not self.context().has_output(self.name()):
            # Deleting the draft release was unsuccessful, nothing to do
            return
        release_data = self.context().step_output(self.name())
        self.github_helper.create_draft_release(
            name=release_data['release name'],
            body=release_data['release body'],
        )


class PostSlackReleaseStep(TransactionalStep):
    def name(self):
        return "Post Slack Release"

    def __init__(
        self,
        slack_cfg_name: str,
        slack_channel: str,
        release_version: str,
        githubrepobranch: GitHubRepoBranch,
    ):
        self.slack_cfg_name = not_empty(slack_cfg_name)
        self.slack_channel = not_empty(slack_channel)
        self.release_version = not_empty(release_version)
        self.githubrepobranch = not_none(githubrepobranch)

    def validate(self):
        semver.parse(self.release_version)

    def apply(self):
        release_notes = self.context().step_output('Publish Release Notes').get('release notes')
        response = post_to_slack(
            release_notes=release_notes,
            github_repository_name=self.githubrepobranch.github_repo_path(),
            slack_cfg_name=self.slack_cfg_name,
            slack_channel=self.slack_channel,
            release_version=self.release_version,
        )
        if response and 'file' in response.keys():
            uploaded_file_id = response['file']['id']
            return {'uploaded file id': uploaded_file_id}
        else:
            raise RuntimeError("Unable to get file id from Slack response")

    def revert(self):
        if not self.context().has_output(self.name()):
            # Posting the release notes was unsuccessful, nothing to revert
            return
        uploaded_file_id = self.context().step_output(self.name()).get('uploaded file id')
        delete_file_from_slack(
            slack_cfg_name=self.slack_cfg_name,
            file_id = uploaded_file_id,
        )


def release_and_prepare_next_dev_cycle(
    githubrepobranch: GitHubRepoBranch,
    repository_version_file_path: str,
    release_version: str,
    repo_dir: str,
    release_commit_callback: str=None,
    next_version_callback: str=None,
    version_operation: str="bump_minor",
    prerelease_suffix: str="dev",
    author_name: str="gardener-ci",
    author_email: str="gardener.ci.user@gmail.com",
    component_descriptor_file_path: str=None,
    slack_cfg_name: str=None,
    slack_channel: str=None,
    rebase_before_release: bool=False,
):
    github_helper = GitHubRepositoryHelper.from_githubrepobranch(githubrepobranch)
    git_helper = GitHelper.from_githubrepobranch(
        githubrepobranch=githubrepobranch,
        repo_path=repo_dir,
    )

    release_commits_step = ReleaseCommitsStep(
        git_helper=git_helper,
        repo_dir=repo_dir,
        release_version=release_version,
        repository_version_file_path=repository_version_file_path,
        repository_branch=githubrepobranch.branch(),
        version_operation=version_operation,
        prerelease_suffix=prerelease_suffix,
        release_commit_callback=release_commit_callback,
        next_version_callback=next_version_callback,
        rebase_before_release=rebase_before_release,
    )

    github_release_step = GitHubReleaseStep(
        github_helper=github_helper,
        githubrepobranch=githubrepobranch,
        repo_dir=repo_dir,
        release_version=release_version,
        component_descriptor_file_path=component_descriptor_file_path,
    )

    release_transaction = Transaction(
        release_commits_step,
        github_release_step,
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

    cleanup_draft_releases_step = CleanupDraftReleaseStep(
        github_helper=github_helper,
        release_version=release_version,
    )

    release_notes_steps = [
        publish_release_notes_step,
        cleanup_draft_releases_step,
    ]

    if slack_cfg_name and slack_channel:
        post_to_slack_step = PostSlackReleaseStep(
            slack_cfg_name=slack_cfg_name,
            slack_channel=slack_channel,
            release_version=release_version,
            githubrepobranch=githubrepobranch,
        )
        release_notes_steps.append(post_to_slack_step)

    release_notes_transaction = Transaction(*release_notes_steps)
    release_notes_transaction.validate()
    if not release_notes_transaction.execute():
        raise RuntimeError('An error occurred while Publishing the Release Notes.')
