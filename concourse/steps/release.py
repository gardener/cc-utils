import abc
import os
import version
import semver
import subprocess
import traceback

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
    github_repo_path,
    draft_release_name_for_version,
)


class TransactionContext(object):
    def __init__(self):
        self._step_outputs = {}

    def step_output(self, step_name: str):
        return self._step_outputs[step_name]

    def set_step_output(self, step_name: str, output):
        if step_name in self._step_outputs.keys():
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
                break

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


class ReleaseCommitStep(TransactionalStep):
    def __init__(
        self,
        git_helper: GitHelper,
        repo_dir: str,
        release_version: str,
        repository_version_file_path: str,
        repository_branch: str,
        rebase_before_release: bool,
        release_commit_callback: str=None,
    ):
        self.git_helper = not_none(git_helper)
        self.repository_branch = not_empty(repository_branch)
        self.release_version = not_empty(release_version)
        self.rebase_before_release = rebase_before_release

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

    def name(self):
        return "Create Release Commit"

    def validate(self):
        existing_dir(self.repo_dir)
        semver.parse(self.release_version)
        if(self.release_commit_callback):
            existing_file(self.release_commit_callback)
        existing_file(self.repository_version_file_path)

    def apply(self):
        if self.rebase_before_release:
            upstream_commit_sha = self.git_helper.fetch_head(
                f'refs/heads/{self.repository_branch}'
            ).hexsha
            self.git_helper.rebase(commit_ish=upstream_commit_sha)

        # clean repository if required
        worktree_dirty = bool(self.git_helper._changed_file_paths())
        if worktree_dirty:
            self.git_helper._stash_changes()

        # update version file
        with open(self.repository_version_file_path, 'w') as f:
            f.write(self.release_version)

        try:
            # call optional release commit callback
            if self.release_commit_callback:
                self._invoke_release_callback(
                    release_commit_callback=self.release_commit_callback,
                    repo_dir=self.repo_dir,
                    release_version=self.release_version,
                )

            release_commit = self.git_helper.index_to_commit(
                message=f'Release {self.release_version}',
            )

            release_commit_sha = release_commit.hexsha

            # forward head to new commit
            self.git_helper.repo.head.set_commit(release_commit_sha)

            # Push release commit to remote
            self.git_helper.push(
                from_ref=release_commit_sha,
                to_ref=self.repository_branch,
                use_ssh=True,
            )
            return {'release commit sha': release_commit_sha}
        finally:
            if worktree_dirty and self.git_helper._has_stash():
                self.git_helper._pop_stash()

    def _invoke_release_callback(
        self,
        release_commit_callback,
        repo_dir,
        release_version,
    ):
        callback_env = os.environ.copy()
        callback_env['REPO_DIR'] = repo_dir
        callback_env['EFFECTIVE_VERSION'] = release_version

        subprocess.run(
            [release_commit_callback],
            check=True,
            env=callback_env,
        )

    def revert(self):
        # TODO: revert commit
        return


class ReleaseTagStep(TransactionalStep):
    def __init__(
        self,
        github_helper: GitHubRepositoryHelper,
        release_version: str,
        author_name: str,
        author_email: str,
    ):
        self.github_helper = not_none(github_helper)
        self.release_version = not_empty(release_version)
        self.author_name = not_empty(author_name)
        self.author_email = not_empty(author_email)

    def validate(self):
        semver.parse(self.release_version)
        # if a tag with the given release version already exists github will not let us
        # create another one
        if self.github_helper.tag_exists(tag_name=self.release_version):
            raise RuntimeError(
                f"Cannot create tag '{self.release_version}' in preparation for release: "
                "Tag already exists"
            )

    def name(self):
        return "Create Release Tag"

    def apply(self):
        release_commit_sha = self.context().step_output('Create Release Commit').get(
            'release commit sha'
        )
        self.github_helper.create_tag(
            tag_name=self.release_version,
            tag_message=f'Release {self.release_version}',
            repository_reference=release_commit_sha,
            author_name=self.author_name,
            author_email=self.author_email
        )

    def revert(self):
        # TODO: Delete tag
        return


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
        if self.component_descriptor_file_path:
            existing_file(self.component_descriptor_file_path)
            with open(self.component_descriptor_file_path) as f:
                # TODO: Proper validation
                not_empty(f.read().strip())

    def apply(
        self,
    ):
        # fetch release notes and generate markdown to catch errors early
        release_notes = fetch_release_notes(
            github_repository_owner=self.githubrepobranch.repo_owner(),
            github_repository_name=self.githubrepobranch.repo_name(),
            github_cfg=self.githubrepobranch.github_config(),
            repo_dir=self.repo_dir,
            github_helper=self.github_helper,
            repository_branch=self.githubrepobranch.branch(),
        )
        release_notes_md = release_notes.to_markdown()

        # Create GitHub-release
        release = self.github_helper.create_release(
            tag_name=self.release_version,
            body=release_notes_md,
            draft=False,
            prerelease=False,
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

        return {
            'release notes': release_notes,
            'release notes markdown': release_notes_md,
        }

    def revert(self):
        #TODO: remove release from GitHub
        return


class PrepareDevCycleStep(TransactionalStep):
    def __init__(
        self,
        github_helper: GitHubRepositoryHelper,
        repo_dir: str,
        repository_version_file_path: str,
        release_version: str,
        version_operation: str,
        prerelease_suffix: str,
    ):
        self.github_helper = not_none(github_helper)
        self.repo_dir=os.path.abspath(not_empty(repo_dir))
        self.repository_version_file_path = not_empty(repository_version_file_path)

        self.release_version = not_empty(release_version)
        self.version_operation = not_empty(version_operation)
        self.prerelease_suffix = not_empty(prerelease_suffix)

    def name(self):
        return "Create Next Cycle Commit"

    def validate(self):
        existing_dir(self.repo_dir)
        existing_file(os.path.join(self.repo_dir, self.repository_version_file_path))

        # perform version ops once to validate args
        self._calculate_next_cycle_dev_version(
            release_version=self.release_version,
            version_operation=self.version_operation,
            prerelease_suffix=self.prerelease_suffix,
        )

    def apply(
        self,
    ):
        next_cycle_dev_version = self._calculate_next_cycle_dev_version(
            release_version=self.release_version,
            version_operation=self.version_operation,
            prerelease_suffix=self.prerelease_suffix,
        )
        # Prepare version file for next dev cycle
        self.github_helper.create_or_update_file(
            file_path=self.repository_version_file_path,
            file_contents=next_cycle_dev_version,
            commit_message=f'Prepare next dev cycle {next_cycle_dev_version}'
        )

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

    def revert(self):
        #TODO: remove created commit
        return


def release_and_prepare_next_dev_cycle(
    githubrepobranch: GitHubRepoBranch,
    repository_version_file_path: str,
    release_version: str,
    repo_dir: str,
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

    github_helper = GitHubRepositoryHelper.from_githubrepobranch(githubrepobranch)
    git_helper = GitHelper.from_githubrepobranch(
        githubrepobranch=githubrepobranch,
        repo_path=repo_dir,
    )

    release_commit_step = ReleaseCommitStep(
        git_helper=git_helper,
        repo_dir=repo_dir,
        release_version=release_version,
        repository_version_file_path=repository_version_file_path,
        repository_branch=githubrepobranch.branch(),
        release_commit_callback=release_commit_callback,
        rebase_before_release=rebase_before_release,
    )

    release_tag_step = ReleaseTagStep(
        github_helper=github_helper,
        release_version=release_version,
        author_email=author_email,
        author_name=author_name,
    )

    github_release_step = GitHubReleaseStep(
        github_helper=github_helper,
        githubrepobranch=githubrepobranch,
        repo_dir=repo_dir,
        release_version=release_version,
        component_descriptor_file_path=component_descriptor_file_path,
    )

    dev_cycle_commit_step = PrepareDevCycleStep(
        github_helper=github_helper,
        repo_dir=repo_dir,
        repository_version_file_path=repository_version_file_path,
        release_version=release_version,
        version_operation=version_operation,
        prerelease_suffix=prerelease_suffix,
    )

    release_transaction = Transaction(
        release_commit_step,
        release_tag_step,
        github_release_step,
        dev_cycle_commit_step,
    )

    release_transaction.validate()
    release_transaction.execute()

    # clean up old draft release
    draft_name = draft_release_name_for_version(release_version)
    draft_release = github_helper.draft_release_with_name(draft_name)

    if draft_release:
        # TODO: clean up ALL previously made draft-releases (just in case)
        info(f'cleaning up draft release {draft_release.name}')
        draft_release.delete()

    # publish release notification to slack
    if slack_cfg_name and slack_channel:
        post_to_slack(
            release_notes=release_context.release_notes(),
            github_repository_name=githubrepobranch.github_repo_path(),
            slack_cfg_name=slack_cfg_name,
            slack_channel=slack_channel,
            release_version=release_version,
        )
