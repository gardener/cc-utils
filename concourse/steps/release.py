import abc
import dataclasses
import logging
import os
import subprocess
import sys
import tempfile
import traceback
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

import gci.componentmodel as cm

import ci.util
from ci.util import (
    existing_file,
    existing_dir,
    not_empty,
    not_none,
)
import concourse.steps.version
import concourse.model.traits.version as version_trait
import cnudie.iter
import cnudie.retrieve
import cnudie.upload
import cnudie.util
import cnudie.validate
import dockerutil
import release_notes.fetch
import release_notes.markdown
import slackclient.util

from gitutil import GitHelper
from github.util import (
    GitHubRepositoryHelper,
    GitHubRepoBranch,
)
from concourse.model.traits.release import (
    ReleaseCommitPublishingPolicy,
    ReleaseNotesPolicy,
)
import model.container_registry as cr
import oci.model

logger = logging.getLogger('step.release')


def component_descriptors(
    component_descriptor_path: str,
):
    have_cd = os.path.exists(component_descriptor_path)

    if have_cd:
        component_descriptor = cm.ComponentDescriptor.from_dict(
                component_descriptor_dict=ci.util.parse_yaml_file(
                    component_descriptor_path,
                ),
                validation_mode=cm.ValidationMode.WARN,
        )
        yield component_descriptor.component
        return
    else:
        raise RuntimeError('did not find component-descriptor')


class TransactionContext:
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
    def name(self):
        pass


class Transaction:
    '''Represents a transaction using `TransactionalStep`s

    After creation, invoke `validate` to have the transaction validate all steps. Invoke
    `execute` to execute all steps. Both operations are done in the original step order.

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
            logger.info(f'validating {step.name()=}')
            step.validate()

    def execute(self):
        executed_steps = list()
        for step in self._steps:
            step_name = step.name()
            logger.info(f'executing {step_name=}')
            executed_steps.append(step)
            try:
                output = step.apply()
                self._context.set_step_output(step_name, output)
            except BaseException as e:
                logger.warning(f'An error occured while running {step_name=} {e=}')
                traceback.print_exc()
                logger.info('the following steps were executed:')
                print()
                for step in executed_steps:
                    print(f' - {step.name()}')
                print()
                logger.warning('manual cleanups may be required')
                return False
        return True


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


class ReleaseCommitStep(TransactionalStep):
    def __init__(
        self,
        git_helper: GitHelper,
        repo_dir: str,
        release_version: str,
        version_interface: version_trait.VersionInterface,
        version_path: str,
        repository_branch: str,
        release_commit_message_prefix: str,
        publishing_policy: ReleaseCommitPublishingPolicy,
        release_commit_callback_image_reference: str,
        release_commit_callback: str=None,
    ):
        self.git_helper = not_none(git_helper)
        self.repository_branch = not_empty(repository_branch)
        self.repo_dir = os.path.abspath(repo_dir)
        self.release_version = not_empty(release_version)
        self.version_interface = version_interface
        self.version_path = version_path
        self.release_commit_message_prefix = release_commit_message_prefix
        self.publishing_policy = publishing_policy
        self.release_commit_callback_image_reference = release_commit_callback_image_reference

        self.release_commit_callback = release_commit_callback

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
        if self.version_path and not os.path.isfile(self.version_path):
            raise ValueError(f'not an existing file: {self.version_path=}')
        if(self.release_commit_callback):
            existing_file(
                os.path.join(
                    self.repo_dir,
                    self.release_commit_callback,
                )
            )

    def apply(self):
        # clean repository if required
        worktree_dirty = bool(self.git_helper._changed_file_paths())
        if worktree_dirty:
            self.git_helper.repo.head.reset(working_tree=True)

        concourse.steps.version.write_version(
            version_interface=self.version_interface,
            version_str=self.release_version,
            path=self.version_path,
        )

        # call optional release commit callback
        if self.release_commit_callback:
            _invoke_callback(
                callback_script_path=self.release_commit_callback,
                repo_dir=self.repo_dir,
                effective_version=self.release_version,
                callback_image_reference=self.release_commit_callback_image_reference,
            )

        release_commit = self.git_helper.index_to_commit(
            message=self._release_commit_message(
                self.release_version,
                self.release_commit_message_prefix
            ),
        )

        # clean up after ourselves
        if self.git_helper._changed_file_paths():
            self.git_helper.repo.head.reset(index=True, working_tree=True)

        self.context().release_commit = release_commit # pass to other steps

        if self.publishing_policy is ReleaseCommitPublishingPolicy.TAG_AND_PUSH_TO_BRANCH:
            # push commit to remote
            self.git_helper.push(
                from_ref=release_commit.hexsha,
                to_ref=self.repository_branch
            )
        elif self.publishing_policy in (
            ReleaseCommitPublishingPolicy.TAG_ONLY,
            ReleaseCommitPublishingPolicy.TAG_AND_MERGE_BACK,
        ):
            # handled when creating all release tags
            pass
        else:
            raise NotImplementedError

        return {
            'release_commit_sha1': release_commit.hexsha,
        }


class CreateTagsStep(TransactionalStep):
    def __init__(
        self,
        tags_to_set,
        github_helper: GitHubRepositoryHelper,
        git_helper: GitHelper,
        publishing_policy: ReleaseCommitPublishingPolicy,
        repository_branch: str,
        merge_commit_message_prefix: str,
    ):
        self.github_helper = github_helper
        self.git_helper = git_helper

        self.publishing_policy = publishing_policy

        self.tags_to_set = tags_to_set

        self.repository_branch = repository_branch
        self.release_tag = tags_to_set[0]

        self.merge_commit_message_prefix = merge_commit_message_prefix

    def name(self):
        return 'Create Tags'

    def validate(self):
        # already happened before the transaction started
        pass

    def apply(
        self,
    ):
        release_commit_step_output = self.context().step_output('Create Release Commit')
        release_commit_sha = release_commit_step_output['release_commit_sha1']

        # depending on the publishing policy either push the release commit to all tag-refs or
        # create tags pointing to the commit on the release-branch
        self.tags_created = []

        if self.publishing_policy in [
            ReleaseCommitPublishingPolicy.TAG_ONLY,
            ReleaseCommitPublishingPolicy.TAG_AND_PUSH_TO_BRANCH,
            ReleaseCommitPublishingPolicy.TAG_AND_MERGE_BACK,
        ]:
            def _push_tag(tag):
                self.git_helper.push(
                    from_ref=release_commit_sha,
                    to_ref=tag,
                )
                self.tags_created.append(tag)

            for tag in self.tags_to_set:
                try:
                    _push_tag(tag)
                except GitCommandError:
                    logger.error(
                        f"Error when trying to push to tag {tag}. Please check whether the tag "
                        'already exists in the repository and consider incrementing the Version '
                        'if it does.\n'
                        'Re-raising error ...'
                    )
                    raise

        else:
            raise NotImplementedError

        if self.publishing_policy is ReleaseCommitPublishingPolicy.TAG_AND_MERGE_BACK:
            def _merge_commit_message(
                tag: str,
                merge_commit_message_prefix: str='',
            ) -> str:
                message = f'Merge release-commit from tag {tag}'
                if merge_commit_message_prefix:
                    return f'{merge_commit_message_prefix} {message}'
                else:
                    return message

            def create_merge_commit(head: git.types.Commit_ish):
                merge_base = self.git_helper.repo.merge_base(
                    head,
                    self.context().release_commit,
                )

                self.git_helper.repo.index.merge_tree(
                    rhs=self.context().release_commit,
                    base=merge_base,
                )

                return self.git_helper.index_to_commit(
                    message=_merge_commit_message(
                        tag=self.release_tag,
                        merge_commit_message_prefix=self.merge_commit_message_prefix,
                    ),
                    parent_commits=(
                        head,
                        self.context().release_commit,
                    ),
                )

            def merge_release_into_current_target_branch_head():
                upstream_commit = self.git_helper.fetch_head(
                    f'refs/heads/{self.repository_branch}'
                )
                self.git_helper.rebase(commit_ish=upstream_commit.hexsha)

                # if repository contains submodules, update worktree to prevent
                # subsequent "git add" from worktree will not overwrite
                # received upstream changes from rebase. for repositories w/o
                # submodules, this is (almost) a no-op
                self.git_helper.repo.submodule_update()

                merge_commit = create_merge_commit(upstream_commit)
                self.context().merge_release_back_to_default_branch_commit = merge_commit

                self.git_helper.push(
                    from_ref=merge_commit.hexsha,
                    to_ref=self.repository_branch
                )

                self.git_helper.repo.head.reset(
                    commit=merge_commit.hexsha,
                    index=True,
                    working_tree=True,
                ) # make sure next dev-cycle commit does not undo the merge-commit

            head_before_merge = self.git_helper.repo.head

            self.git_helper.repo.head.reset(
                commit=self.context().release_commit,
                index=True,
                working_tree=True,
            )

            try:
                merge_release_into_current_target_branch_head()

            except (GitCommandError, RuntimeError):
                # should only occur on merge conflicts or head-update during release
                logger.warning(f'Merging release-commit from tag {self.release_tag} failed.')
                traceback.print_exc()

                self.git_helper.repo.head.reset(
                    commit=head_before_merge.commit.hexsha,
                    index=True,
                    working_tree=True,
                ) # clean repo for subsequent steps

                # do not fail release-step here as release-tag is created already and next dev-cycle
                # commit must still be processed

        return {
            'release_tag': self.release_tag,
            'tags': self.tags_to_set[1:],
        }


class NextDevCycleCommitStep(TransactionalStep):
    def __init__(
        self,
        git_helper: GitHelper,
        repo_dir: str,
        release_version: str,
        version_interface: version_trait.VersionInterface,
        version_path: str,
        repository_branch: str,
        version_operation: str,
        prerelease_suffix: str,
        publishing_policy: ReleaseCommitPublishingPolicy,
        next_cycle_commit_message_prefix: str=None,
        next_version_callback: str=None,
    ):
        self.git_helper = not_none(git_helper)
        self.repository_branch = not_empty(repository_branch)
        self.repo_dir = os.path.abspath(repo_dir)
        self.release_version = not_empty(release_version)
        self.version_interface = version_interface
        self.version_path = version_path
        self.version_operation = not_empty(version_operation)
        self.prerelease_suffix = not_empty(prerelease_suffix)
        self.publishing_policy = publishing_policy
        self.next_cycle_commit_message_prefix = next_cycle_commit_message_prefix

        self.next_version_callback = next_version_callback

    def _next_dev_cycle_commit_message(self, version: str, message_prefix: str):
        message = f'Prepare next Dev Cycle {version}'
        if message_prefix:
            message = f'{message_prefix} {message}'
        return message

    def name(self):
        return 'Create next development cycle commit'

    def validate(self):
        existing_dir(self.repo_dir)
        version.parse_to_semver(self.release_version)
        if self.next_version_callback:
            existing_file(
                os.path.join(
                    self.repo_dir,
                    self.next_version_callback,
                )
            )

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
            elif self.publishing_policy is ReleaseCommitPublishingPolicy.TAG_AND_MERGE_BACK:
                reset_to = getattr(
                    self.context(),
                    'merge_release_back_to_default_branch_commit',
                    'HEAD',
                ) # release continues even if merging-back release-commit fails
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
        logger.info(f'{next_version=}')

        concourse.steps.version.write_version(
            version_interface=self.version_interface,
            version_str=next_version,
            path=self.version_path,
        )

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
        elif self.publishing_policy is ReleaseCommitPublishingPolicy.TAG_AND_MERGE_BACK:
            parent_commit = getattr(
                self.context(),
                'merge_release_back_to_default_branch_commit',
                None,
            ) # release continues even if merging-back release-commit fails

            if parent_commit:
                parent_commits = [parent_commit]
            else:
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


class GitHubReleaseStep(TransactionalStep):
    def __init__(
        self,
        github_helper: GitHubRepositoryHelper,
        githubrepobranch: GitHubRepoBranch,
        repo_dir: str,
        component_name: str,
        release_version: str,
    ):
        self.github_helper = not_none(github_helper)
        self.githubrepobranch = githubrepobranch
        self.release_version = not_empty(release_version)
        self.repo_dir = repo_dir
        self.component_name = component_name

    def name(self):
        return "Create Release"

    def validate(self):
        version.parse_to_semver(self.release_version)

    def apply(
        self,
    ):
        create_tags_step_output = self.context().step_output('Create Tags')
        release_tag = create_tags_step_output['release_tag']

        # github3.py expects the tags's name, not the whole ref
        if release_tag.startswith('refs/tags/'):
            release_tag = release_tag[10:]
        else:
            raise RuntimeError(
                f'unexpected {release_tag=}. Expected a ref, e.g. `refs/tags/foo`'
            )

        # Create GitHub-release
        if release := self.github_helper.draft_release_with_name(f'{self.release_version}-draft'):
            self.github_helper.promote_draft_release(
                draft_release=release,
                release_tag=release_tag,
                release_version=self.release_version,
                component_name=self.component_name,
            )
        else:
            release = self.github_helper.create_release(
                tag_name=release_tag,
                body="",
                draft=False,
                prerelease=False,
                name=self.release_version,
                component_name=self.component_name,
            )

        return {
            'release_tag_name': release_tag,
        }


class UploadComponentDescriptorStep(TransactionalStep):
    def __init__(
        self,
        github_helper: GitHubRepositoryHelper,
        components: tuple[cm.Component],
        release_on_github: bool,
        mapping_config: cnudie.util.OcmLookupMappingConfig,
    ):
        self.github_helper = not_none(github_helper)
        self.components = components
        self.release_on_github = release_on_github
        self.ocm_lookup = cnudie.retrieve.oci_component_descriptor_lookup(
            ocm_repository_lookup=mapping_config,
        )

    def name(self):
        return "Upload Component Descriptor"

    def validate(self):
        components: tuple[cm.Component] = tuple(
            cnudie.util.iter_sorted(
                self.components,
            )
        )

        if not components:
            ci.util.fail('No component descriptor found')

        def iter_components():
            for component in components:
                yield from cnudie.iter.iter(
                    lookup=self.ocm_lookup,
                    component=component,
                    recursion_depth=0,
                )

        validation_errors = tuple(
            cnudie.validate.iter_violations(nodes=iter_components())
        )

        if not validation_errors:
            return

        for validation_error in validation_errors:
            logger.error(validation_error.as_error_message)

        logger.error(
            'there were validation-errors in component-descriptor - aborting release (see above)'
        )
        exit(1)

    def apply(self):
        if self.release_on_github:
            create_release_step_output = self.context().step_output('Create Release')
            release_tag_name = create_release_step_output['release_tag_name']

        components = tuple(cnudie.util.iter_sorted(self.components))
        components_by_id = {
            component.identity(): component
            for component in components
        }

        # todo: mv to `validate`
        def resolve_dependencies(component: cm.Component):
            for _ in cnudie.retrieve.components(
                component=component,
                component_descriptor_lookup=self.ocm_lookup,
            ):
                pass

        def upload_component_descriptors(components: tuple[cm.Component]):
            for component in components:
                try:
                    resolve_dependencies(component=component)
                except oci.model.OciImageNotFoundException as e:
                    logger.error(
                        f'{component.name=} {component.version=} has dangling component-reference'
                    )
                    raise e

                component = components_by_id[component.identity()]

                tgt_ref = cnudie.util.target_oci_ref(component=component)

                logger.info(f'publishing OCM-Component-Descriptor to {tgt_ref=}')
                cnudie.upload.upload_component_descriptor(
                    component_descriptor=component,
                )

        upload_component_descriptors(components=components)

        def upload_component_descriptor_as_release_asset():
            try:
                release = self.github_helper.repository.release_from_tag(release_tag_name)

                for component in components:
                    component_descriptor = cm.ComponentDescriptor(
                        component=component,
                        meta=cm.Metadata(),
                        signatures=[],
                    )

                    descriptor_str = yaml.dump(
                        data=dataclasses.asdict(component_descriptor),
                        Dumper=cm.EnumValueYamlDumper,
                    )

                    normalized_component_name = component.name.replace('/', '_')
                    asset_name = f'{normalized_component_name}.component_descriptor.cnudie.yaml'
                    release.upload_asset(
                        content_type='application/x-yaml',
                        name=asset_name,
                        asset=descriptor_str.encode('utf-8'),
                        label=asset_name,
                    )
            except ConnectionError:
                logger.warning('Unable to attach component-descriptors to release as release-asset.')

        if self.release_on_github:
            upload_component_descriptor_as_release_asset()


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
                logger.info(f'Deleted {release.name=}')
            else:
                logger.warning(f'Could not delete {release.name=}')
        return


class PostSlackReleaseStep(TransactionalStep):
    def name(self):
        return f"Post Slack Release ({self.slack_channel})"

    def __init__(
        self,
        slack_cfg_name: str,
        slack_channel: str,
        release_version: str,
        release_notes_markdown: str,
        githubrepobranch: GitHubRepoBranch,
    ):
        self.slack_cfg_name = not_empty(slack_cfg_name)
        self.slack_channel = not_empty(slack_channel)
        self.release_version = not_empty(release_version)
        self.githubrepobranch = not_none(githubrepobranch)
        self.release_notes_markdown = not_none(release_notes_markdown)

    def validate(self):
        version.parse_to_semver(self.release_version)

    def apply(self):
        responses = slackclient.util.post_to_slack(
            release_notes_markdown=self.release_notes_markdown,
            github_repository_name=self.githubrepobranch.github_repo_path(),
            slack_cfg_name=self.slack_cfg_name,
            slack_channel=self.slack_channel,
            release_version=self.release_version,
        )

        for response in responses:
            if response and response.get('file', None):
                uploaded_file_id = response.get('file').get('id')
                logger.info(f'uploaded {uploaded_file_id=} to slack')
            else:
                raise RuntimeError('Unable to get file id from Slack response')
        logger.info('successfully posted contents to slack')


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


def _calculate_tags(
    release_version: str,
    github_release_tag: dict,
    git_tags: list,
) -> typing.Sequence[str]:
    tag_template_vars = {'VERSION': release_version}

     # render tag-templates
    github_release_tag_candidate = github_release_tag['ref_template'].format(
        **tag_template_vars
    )
    git_tag_candidates = [
        tag_template['ref_template'].format(**tag_template_vars)
        for tag_template in git_tags
    ]

    return [github_release_tag_candidate] + git_tag_candidates


def _conflicting_tags(
    tags_to_set: typing.Sequence[str],
    github_helper,
):
    return set(
        tag
        for tag in tags_to_set
        if github_helper.tag_exists(tag.removeprefix('refs/tags/'))
    )


def release_and_prepare_next_dev_cycle(
    component_name: str,
    githubrepobranch: GitHubRepoBranch,
    release_commit_publishing_policy: str,
    release_notes_policy: str,
    release_version: str,
    repo_dir: str,
    version_path: str,
    version_interface: version_trait.VersionInterface,
    git_tags: list,
    github_release_tag: dict,
    release_commit_callback_image_reference: str,
    mapping_config,
    component_descriptor_path: str=None,
    next_cycle_commit_message_prefix: str=None,
    next_version_callback: str=None,
    prerelease_suffix: str="dev",
    rebase_before_release: bool=False,
    release_on_github: bool=True,
    release_commit_callback: str=None,
    release_commit_message_prefix: str=None,
    merge_release_to_default_branch_commit_message_prefix: str=None,
    slack_channel_configs: list=[],
    version_operation: str='bump_minor',
):
    components = tuple(
        component_descriptors(
            component_descriptor_path=component_descriptor_path,
        )
    )
    if len(components) == 1:
        component = components[0]
    elif len(components) == 0:
        logger.fatal('no component-descriptors could be found - aborting release')
        exit(1)
    else:
        for component in components:
            if component.name == component_name:
                break # found it - `component` is used later
        else:
            logger.fatal(f'could not find component w/ {component_name=} - aborting release')
            exit(1)

    version.parse_to_semver(release_version)

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

    # make sure that desired tag(s) do not already exist. If configured to do so, increment
    # in case of collisions

    tags_to_set = _calculate_tags(release_version, github_release_tag, git_tags)
    logger.info(f'Making sure that required tags {tags_to_set} do not exist, yet')
    if (existing_tags := _conflicting_tags(tags_to_set, github_helper)):
        logger.error(
            f'conflict: {existing_tags=}. Increment version or delete tags.'
        )
        exit(1)

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
        version_interface=version_interface,
        version_path=version_path,
        repository_branch=githubrepobranch.branch(),
        release_commit_message_prefix=release_commit_message_prefix,
        release_commit_callback=release_commit_callback,
        release_commit_callback_image_reference=release_commit_callback_image_reference,
        publishing_policy=release_commit_publishing_policy,
    )
    step_list.append(release_commit_step)

    create_tag_step = CreateTagsStep(
        tags_to_set=tags_to_set,
        git_helper=git_helper,
        github_helper=github_helper,
        publishing_policy=release_commit_publishing_policy,
        repository_branch=githubrepobranch.branch(),
        merge_commit_message_prefix=merge_release_to_default_branch_commit_message_prefix,
    )
    step_list.append(create_tag_step)

    if version_operation != version.NOOP:
        next_cycle_commit_step = NextDevCycleCommitStep(
            git_helper=git_helper,
            repo_dir=repo_dir,
            release_version=release_version,
            version_interface=version_interface,
            version_path=version_path,
            repository_branch=githubrepobranch.branch(),
            version_operation=version_operation,
            prerelease_suffix=prerelease_suffix,
            next_version_callback=next_version_callback,
            publishing_policy=release_commit_publishing_policy,
            next_cycle_commit_message_prefix=next_cycle_commit_message_prefix,
        )
        step_list.append(next_cycle_commit_step)

    if release_on_github:
        github_release_step = GitHubReleaseStep(
            github_helper=github_helper,
            githubrepobranch=githubrepobranch,
            repo_dir=repo_dir,
            component_name=component_name,
            release_version=release_version,
        )
        step_list.append(github_release_step)

    upload_component_descriptor_step = UploadComponentDescriptorStep(
        github_helper=github_helper,
        components=components,
        release_on_github=release_on_github,
        mapping_config=mapping_config,
    )

    step_list.append(upload_component_descriptor_step)

    release_transaction = Transaction(
        ctx=transaction_ctx,
        steps=step_list,
    )

    release_transaction.validate()
    if not release_transaction.execute():
        raise RuntimeError('An error occurred while creating the Release.')

    cleanup_draft_releases_step = TryCleanupDraftReleasesStep(
        github_helper=github_helper,
    )

    cleanup_draft_releases_transaction = Transaction(
        ctx=transaction_ctx,
        steps=(cleanup_draft_releases_step,),
    )

    if not cleanup_draft_releases_transaction.execute():
        logger.warning('An error occured while cleaning up draft releases')

    if release_notes_policy == ReleaseNotesPolicy.DISABLED:
        return logger.info('release notes were disabled - skipping')
    elif release_notes_policy == ReleaseNotesPolicy.DEFAULT:
        pass
    else:
        raise NotImplementedError(release_notes_policy)

    if release_on_github:
        version_lookup = cnudie.retrieve.version_lookup(
            ocm_repository_lookup=mapping_config,
        )
        component_descriptor_lookup = cnudie.retrieve.create_default_component_descriptor_lookup(
            ocm_repository_lookup=mapping_config,
        )

        release_note_blocks = release_notes.fetch.fetch_release_notes(
            repo_path=repo_dir,
            component=component,
            version_lookup=version_lookup,
            component_descriptor_lookup=component_descriptor_lookup,
            current_version=release_version,
        )
        release_notes_markdown = '\n'.join(
            str(i) for i in release_notes.markdown.render(release_note_blocks)
        ) or 'no release notes available'

        if (component_resources_markdown := release_notes.markdown.release_note_for_ocm_component(
            component=component,
        )):
            release_notes_markdown += '\n\n' + component_resources_markdown

        github_helper.update_release_notes(
            tag_name=release_version,
            body=release_notes_markdown,
            component_name=component.name,
        )
        try:
            git_helper.push('refs/notes/commits', 'refs/notes/commits')
        except:
            exception = sys.exception()
            logger.warning(f'There was an error when pushing created git-notes: {exception}')

    if slack_channel_configs:
        if not release_on_github:
            raise RuntimeError('Cannot post to slack without a github release')

        all_slack_releases_successful = True
        for slack_cfg in slack_channel_configs:
            slack_cfg_name = slack_cfg['slack_cfg_name']
            slack_channel = slack_cfg['channel_name']
            post_to_slack_step = PostSlackReleaseStep(
                slack_cfg_name=slack_cfg_name,
                slack_channel=slack_channel,
                release_version=release_version,
                release_notes_markdown=release_notes_markdown,
                githubrepobranch=githubrepobranch,
            )
            slack_transaction = Transaction(
                ctx=transaction_ctx,
                steps=(post_to_slack_step,),
            )
            slack_transaction.validate()
            all_slack_releases_successful = (
                all_slack_releases_successful and slack_transaction.execute()
            )
        if not all_slack_releases_successful:
            raise RuntimeError('An error occurred while posting the release notes to Slack.')
