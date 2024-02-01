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


class TransactionContext:
    release_commit: str = None

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


class UploadComponentDescriptorStep(TransactionalStep):
    def __init__(
        self,
        github_helper: GitHubRepositoryHelper,
        components: tuple[cm.Component],
        release_on_github: bool,
        mapping_config: cnudie.util.OcmLookupMappingConfig,
        github_release_tag: str=None,
    ):
        self.github_helper = not_none(github_helper)
        self.components = components
        self.release_on_github = release_on_github
        self.github_release_tag = github_release_tag
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
            release_tag_name = self.github_release_tag.removeprefix('refs/tags/')

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


def release_and_prepare_next_dev_cycle(
    component_descriptor,
    github_helper: GitHubRepositoryHelper,
    git_helper: GitHelper,
    githubrepobranch: GitHubRepoBranch,
    release_commit: git.Commit,
    release_notes_policy: str,
    release_version: str,
    repo_dir: str,
    git_tags: list,
    release_tag: str,
    github_release_tag: dict,
    mapping_config,
    release_on_github: bool=True,
    slack_channel_configs: list=[],
):
    component = component_descriptor.component
    version.parse_to_semver(release_version)

    transaction_ctx = TransactionContext() # shared between all steps/trxs
    transaction_ctx.release_commit = release_commit

    release_notes_policy = ReleaseNotesPolicy(release_notes_policy)

    step_list = []

    upload_component_descriptor_step = UploadComponentDescriptorStep(
        github_helper=github_helper,
        components=(component,),
        release_on_github=release_on_github,
        github_release_tag=release_tag,
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
        logger.info('release notes were disabled - skipping')
        return getattr(transaction_ctx, 'merge_release_back_to_default_branch_commit', 'HEAD')
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

    try:
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
    except Exception:
        logger.warning('an exception occurred whilst trying to post release-notes to slack')
        traceback.print_exc()

    return getattr(transaction_ctx, 'merge_release_back_to_default_branch_commit', 'HEAD')


def rebase(
    git_helper,
    branch: str,
):
    logging.info('Rebasing..')
    upstream_commit_sha = git_helper.fetch_head(
        f'refs/heads/{branch}'
    ).hexsha
    git_helper.rebase(commit_ish=upstream_commit_sha)


def have_tag_conflicts(
    github_helper,
    tags,
):
    found_tags = 0
    for tag in tags:
        if github_helper.tag_exists(tag.removeprefix('refs/tags/')):
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

    commit_message = f'Prepare next Development Cycle {version}'
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

    # XXX remove?
    return {
        'next cycle commit sha': next_cycle_commit.hexsha,
    }


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
    github_helper,
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
        commit=release_commit.hexsha,
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
    git_repo.submodule_update()

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


def github_release(
    github_helper: GitHubRepositoryHelper,
    release_tag: str,
    release_version: str,
    component_name: str,
):
    # github-api expects unqualified tagname
    release_tag = release_tag.removeprefix('refs/tags/')

    if release := github_helper.draft_release_with_name(f'{release_version}-draft'):
        github_helper.promote_draft_release(
            draft_release=release,
            release_tag=release_tag,
            release_version=release_version,
            component_name=component_name,
        )
    else:
        release = github_helper.create_release(
            tag_name=release_tag,
            body='',
            draft=False,
            prerelease=False,
            name=release_version,
            component_name=component_name,
        )
