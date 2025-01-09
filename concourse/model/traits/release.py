# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import enum
import dataclasses
import textwrap
import typing

import dacite

import version
import ci.util
from concourse.model.job import (
    JobVariant,
)
from concourse.model.step import (
    PipelineStep,
    PrivilegeMode,
    PullRequestNotificationPolicy,
)
from concourse.model.base import (
  AttributeSpec,
  EnumWithDocumentation,
  EnumValueWithDocumentation,
  Trait,
  TraitTransformer,
  ScriptType,
)
from model.base import(
    ModelValidationError,
)
import concourse.model.traits.images

OciImageCfg = concourse.model.traits.images.OciImageCfg


class NextVersion(EnumWithDocumentation):
    BUMP_MAJOR = EnumValueWithDocumentation(
        value='bump_major',
        doc='Increments the major version of the next development cycle',
    )

    BUMP_MINOR = EnumValueWithDocumentation(
        value='bump_minor',
        doc='Increments the minor version of the next development cycle',
    )

    BUMP_PATCH = EnumValueWithDocumentation(
        value='bump_patch',
        doc='Increments the patch version of the next development cycle',
    )

    NOOP = EnumValueWithDocumentation(
        value='noop',
        doc='No change to the next development cycle version done',
    )


class ReleaseNotesPolicy(EnumWithDocumentation):
    DEFAULT = EnumValueWithDocumentation(
        value='default',
        doc='Create release notes and add them to the GitHub release.',
    )
    DISABLED = EnumValueWithDocumentation(
        value='disabled',
        doc='Do not create release notes.',
    )


class ReleaseCommitPublishingPolicy(EnumWithDocumentation):
    TAG_AND_PUSH_TO_BRANCH = EnumValueWithDocumentation(
        value='push_to_branch',
        doc='publish release tag to branch',
    )

    TAG_ONLY = EnumValueWithDocumentation(
        value='tag_only',
        doc='publish release tag to dead-end',
    )

    TAG_AND_MERGE_BACK = EnumValueWithDocumentation(
        value='tag_and_merge_back',
        doc='publish release tag to dead-end and merge back release commit to default branch',
    )
    SKIP = EnumValueWithDocumentation(
        value='skip',
        doc='neither create, nor publish either of release- and bump-commits',
    )


class TagConflictAction(enum.StrEnum):
    IGNORE = 'ignore'
    FAIL = 'fail'
    INCREMENT_PATCH_VERSION = 'increment-patch-version'


@dataclasses.dataclass(kw_only=True)
class Asset:
    '''
    base class for release assets. Not intended to be instantiated

    Assets are references to data from build (build log, files, or directory trees) that are
    to be included in Component-Descriptor as resources. Each asset is added as one resource.

    Parameters for OCM Resource:

    - ocm_labels: OCM-Labels to be added to resource
    - name: resource name
    - artefact_type: resource type
    - purposes: value for label `gardener.cloud/purposes`

    Other Parameters:

    - step_name: pipelinestep-name of data-source
    - comment: unused
    - type: asset-type - overwritten by subclasses
    - github_asset_name: name to use for github asset (must be unique per release)

    Purpose-labels will be added as OCM-Label `gardener.cloud/purposes`.
    They are used to identify (in a machine-readable manner) the purpose / semantics
    of the included assets. Values are not (yet) standardised. It is recommended to use the
    following labels (multiple may be specified):

    build
    codecoverage
    integrationtest
    linter
    sast          static code analysis results
    test
    unittest
    '''
    ocm_labels: list[dict[str, object]] = dataclasses.field(default_factory=list)
    type: str = None # must overwrite
    name: str = None
    step_name: str
    artefact_type: str = 'application/data'
    artefact_extra_id: dict[str, str] = dataclasses.field(default_factory=dict)
    purposes: list[str] = dataclasses.field(default_factory=list)
    comment: str | None = None
    github_asset_name: str | None = None

    def __post_init__(self):
        if self.purposes:
            self.ocm_labels.append({
                'name': 'gardener.cloud/purposes',
                'value': self.purposes,
            })

        if self.comment:
            self.ocm_labels.append({
                'name': 'gardener.cloud/comment',
                'value': self.comment,
            })


class FileAssetMode(enum.StrEnum):
    TAR = 'tar'
    SINGLE_FILE = 'single-file'


@dataclasses.dataclass(kw_only=True)
class BuildstepFileAsset(Asset):
    '''
    A reference to files from a build-step to be added to OCM Component Descriptor as resource.

    `step_output_dir` specifies the name of the output directory from which to read specified files.
    It must be declared from the build-step (step_name parameter) using the `output_dir`
    parameter.

    `path` is interpreted relative to `step_output_dir`. Using Unix globbing, any matching files
    will be included (use `*` to include all, except files starting with period (`.`) character).

    `prefix` will be removed from fnames in tar (similar to tar's -C option). Only effective in
    TAR mode (ignored otherwise). If any path does not start w/ prefix, this is not considered an
    error. Instead, such paths will silently be kept unchanged.

    By default, matching files will be wrapped in a compressed TAR archive (controlled by `mode`
    attribute). If mode is set to `single-file`, `path` must match exactly one regular file.

    In either case, it is considered an error if no matching files are found.
    '''
    type: str = 'build-step-file'
    step_output_dir: str
    path: str # rel-path; globbing is allowed
    prefix: str | None = None
    mode: FileAssetMode = FileAssetMode.TAR

    def __post_init__(self):
        super().__post_init__()

        if not self.name:
            self.name = f'{self.step_name}-build-step-file'


@dataclasses.dataclass(kw_only=True)
class BuildstepLogAsset(Asset):
    '''
    An (additional) release asset to be included as part of a release. Hardcoded for the
    special-case of including build-step-logs as resource in released component-descriptor
    with a srcRef to main repository (all of which might be made more flexible if needed).
    '''
    type: str = 'build-step-log'
    artefact_type = 'text/plain'

    def __post_init__(self):
        super().__post_init__()

        if not self.name:
            self.name = f'{self.step_name}-build-step-log'


ATTRIBUTES = (
    AttributeSpec.optional(
        name='assets',
        default=[],
        doc='''
        additional release-assets to publish.
        ''',
        type=list[BuildstepLogAsset | BuildstepFileAsset, ...],
    ),
    AttributeSpec.optional(
        name='nextversion',
        default=NextVersion.BUMP_MINOR.value,
        doc='specifies how the next development version is to be calculated',
        type=NextVersion,
    ),
    AttributeSpec.optional(
        name='release_callback',
        default=None,
        doc='''
        an optional callback that is called during release commit creation. The callback is passed
        the absolute path to the main repository's work tree via environment variable `REPO_DIR`.
        Any changes left inside the worktree are added to the resulting release commit.
        ''',
    ),
    AttributeSpec.optional(
        name='release_callback_image_reference',
        default=None,
        doc='''
        if specified, the release_callback will be run in a virtualisation container using the
        chosen container image (if not specified, the callback is run as a subprocess)
        ''',
        type=OciImageCfg,
    ),
    AttributeSpec.optional(
        name='rebase_before_release',
        default=False,
        doc='''
        whether or not a rebase against latest branch head should be done before publishing
        release commits.
        ''',
        type=bool,
    ),
    AttributeSpec.optional(
        name='next_version_callback',
        default=None,
        doc='''
        an optional callback that is called during next version commit creation.
        The callback is passed the absolute path to the main repository's work tree via environment
        variable `REPO_DIR`.
        Any changes left inside the worktree are added to the commit bumping the version
        immediately after the release-commit.
        ''',
    ),
    AttributeSpec.optional(
        name='release_notes_policy',
        default=ReleaseNotesPolicy.DEFAULT.value,
        doc='''
        configures the release notes handling policy
        ''',
        type=ReleaseNotesPolicy,
    ),
    AttributeSpec.optional(
        name='release_commit_publishing_policy',
        default=ReleaseCommitPublishingPolicy.TAG_AND_PUSH_TO_BRANCH,
        doc='''
        configures how the release commit should be published
        ''',
        type=ReleaseCommitPublishingPolicy,
    ),
    AttributeSpec.optional(
        name='commit_message_prefix',
        default=None,
        doc='''
        an optional prefix for release commit messages
        ''',
        type=str,
    ),
    AttributeSpec.optional(
        name='next_version_commit_message_prefix',
        default=None,
        doc='''
        an optional prefix for the commit message of the commit bumping the release version
        immediately after the release-commit
        ''',
        type=str,
    ),
    AttributeSpec.optional(
        name='merge_release_to_default_branch_commit_message_prefix',
        default=None,
        doc='''
        an optional prefix for the merge-commit message when merging back release-commit from tag
        to default branch
        ''',
        type=str,
    ),
    AttributeSpec.optional(
        name='git_tags',
        default=[{'ref_template': 'refs/tags/{VERSION}'}],
        doc='''
        a list of tags to tag the release commit with, **at least one**. The following
        placeholders are available:

        - {VERSION}: The version to be released (i.e. the 'effective version')

        The first tag will be used to create the GitHub-release.
        ''',
        type=list
    ),
    AttributeSpec.optional(
        name='release_on_github',
        default=True,
        doc='''
        if true, a github release is published.
        ''',
        type=bool
    ),
    AttributeSpec.optional(
        name='on_tag_conflict',
        default=TagConflictAction.IGNORE,
        doc=textwrap.dedent('''\
            specifies the action to take if the tag to be pushed (`refs/tags/<effective-version>`)
            already exists (relevant, if `release_on_github` is not set to `False`).
            Such cases can occur if pushing of "bump-commit" after previous release failed.
            Default value is chosen for backwards-compatibility.
            `fail` will lead to the `version` step to fail (which will shorten the time to
            discover this error, and thus save time)
            `increment-patch-version` will increment effective version's patchlevel (and thus
            avoid a conflict)
        '''),
        type=TagConflictAction,
    ),
)


class ReleaseTrait(Trait):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @classmethod
    def _attribute_specs(cls):
        return ATTRIBUTES

    @property
    def assets(self) -> list[BuildstepLogAsset | BuildstepFileAsset, ...]:
        if not (raw_assets := self.raw.get('assets')):
            return []

        def deserialise(raw_asset: dict):
            if not 'type' in raw_asset:
                raise ModelValidationError(
                    f'`type` must be present for {raw_asset=}',
                )

            if (typename := raw_asset['type']) == BuildstepFileAsset.type:
                data_class = BuildstepFileAsset
            elif typename == BuildstepLogAsset.type:
                data_class = BuildstepLogAsset
            else:
                raise ModelValidationError(typename)

            return dacite.from_dict(
                data_class=data_class,
                data=raw_asset,
                config=dacite.Config(
                    cast=(enum.Enum,),
                ),
            )

        return [
            deserialise(raw_asset)
            for raw_asset in raw_assets
        ]

    def nextversion(self):
        return self.raw['nextversion']

    def release_callback_path(self):
        return self.raw['release_callback']

    def release_callback_image_reference(self) -> typing.Optional[OciImageCfg]:
        if not (raw := self.raw.get('release_callback_image_reference')):
            return None

        return OciImageCfg(raw_dict=raw)

    def next_version_callback_path(self):
        return self.raw['next_version_callback']

    def rebase_before_release(self):
        return self.raw['rebase_before_release']

    def release_notes_policy(self) -> ReleaseNotesPolicy:
        return ReleaseNotesPolicy(self.raw.get('release_notes_policy'))

    def release_commit_publishing_policy(self) -> ReleaseCommitPublishingPolicy:
        return ReleaseCommitPublishingPolicy(self.raw['release_commit_publishing_policy'])

    def release_commit_message_prefix(self) -> str:
        return self.raw.get('commit_message_prefix')

    def next_cycle_commit_message_prefix(self) -> str:
        return self.raw.get('next_version_commit_message_prefix')

    def merge_release_to_default_branch_commit_message_prefix(self) -> str:
        return self.raw.get('merge_release_to_default_branch_commit_message_prefix')

    def git_tags(self):
        '''
        all tags to be created in addition to the "github-release-tag" (without the gh-release-tag)
        '''
        return self.raw.get('git_tags')[1:]

    def github_release_tag(self):
        if tags := self.raw.get('git_tags'):
            return tags[0]
        return None

    def release_on_github(self) -> bool:
        return self.raw['release_on_github']

    @property
    def on_tag_conflict(self) -> TagConflictAction:
        return TagConflictAction(self.raw['on_tag_conflict'])

    def validate(self):
        super().validate()
        if self.nextversion() == version.NOOP and self.next_version_callback_path():
            raise ModelValidationError(
                f'not possible to configure "next_version_callback" if version is "{version.NOOP}"'
            )
        if not self.github_release_tag():
            raise ModelValidationError('At least one tag must be configured for the release.')

        # ensure the form of the first tag is as expected - otherwise no release can be created
        if not self.github_release_tag()['ref_template'].startswith('refs/tags/'):
            raise ModelValidationError(
                "The first release-tag must be of the form 'refs/tags/<tagname>'."
            )

    # by default, all Trait merge list-arg-defaults. Disable to support overriding the default
    # release-tag
    def _apply_defaults(self, raw_dict):
        self.raw = ci.util.merge_dicts(
            self._defaults_dict(),
            raw_dict,
            list_semantics=None,
        )

    def transformer(self):
        return ReleaseTraitTransformer(trait=self)


class ReleaseTraitTransformer(TraitTransformer):
    name = 'release'

    def __init__(self, trait: ReleaseTrait, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.trait = trait

    def inject_steps(self):
        if self.trait.release_callback_image_reference():
            # we need privileged container in order to run callback in container
            privilege_mode = PrivilegeMode.PRIVILEGED
        else:
            privilege_mode = PrivilegeMode.UNPRIVILEGED

        # inject 'release' step
        self.release_step = PipelineStep(
            name='release',
            raw_dict={
                'privilege_mode': privilege_mode,
            },
            is_synthetic=True,
            pull_request_notification_policy=PullRequestNotificationPolicy.NO_NOTIFICATION,
            injecting_trait_name=self.name,
            script_type=ScriptType.PYTHON3,
        )
        self.release_step.set_timeout('2h')
        yield self.release_step

    def process_pipeline_args(self, pipeline_args: JobVariant):
        # we depend on all other steps
        for step in pipeline_args.steps():
            self.release_step._add_dependency(step)

            if step.name == 'helmcharts':
                self.release_step.add_input('helmcharts', 'helmcharts')

        # a 'release job' should only be triggered automatically if explicitly configured
        main_repo = pipeline_args.main_repository()
        if main_repo:
            if 'trigger' not in pipeline_args.raw['repo']:
                main_repo._trigger = False

        # validate publishing to github is disabled if release-commit is disabled
        if self.trait.release_commit_publishing_policy() is ReleaseCommitPublishingPolicy.SKIP:
            if self.trait.release_on_github():
                raise ModelValidationError(
                    'must disable releasing to github if release-commit is set to `skip`',
                )

        # validate assets if present
        for asset in self.trait.assets:
            if not pipeline_args.has_step(asset.step_name):
                raise ValueError(textwrap.dedent(f'''\
                    {asset=}\'s step_name refers to an absent build-step. If the step in question is
                    declared branch-specifically, i.e. via `branch.cfg`, and the current branch is
                    going to be merged with a branch declaring the pipeline step, this error can be
                    safely ignored, iff the branch is transient only (not used for release).
                '''))

            if isinstance(asset, BuildstepFileAsset):
                asset: BuildstepFileAsset
                step = pipeline_args.step(asset.step_name)

                if not asset.step_output_dir in step.outputs().values():
                  raise ModelValidationError(
                    f'{step.name=} does not declare {asset.step_output_dir=}',
                  )

                self.release_step.add_input(
                  name=asset.step_output_dir,
                  variable_name=asset.step_output_dir,
                )

    @classmethod
    def dependencies(cls):
        return {'version', 'component_descriptor'}

    @classmethod
    def order_dependencies(cls):
        return {'publish'}
