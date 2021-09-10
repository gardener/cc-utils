# Copyright (c) 2019-2020 SAP SE or an SAP affiliate company. All rights reserved. This file is
# licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import typing

import version
import ci.util
from concourse.model.job import (
    JobVariant,
)
from concourse.model.step import (
    PipelineStep,
    PrivilegeMode,
    StepNotificationPolicy,
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


ATTRIBUTES = (
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
        name='git_tags',
        default=[{'ref_template': 'refs/tags/{VERSION}'}],
        doc='''
        a list of tags to tag the release commit with, **at least one**. The following
        placeholders are available:

        - {VERSION}: The version to be released (i.e. the 'effective version')

        The first tag will be used to create the GitHub-release.
        ''',
        type=list
    )
)


class ReleaseTrait(Trait):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @classmethod
    def _attribute_specs(cls):
        return ATTRIBUTES

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

    def git_tags(self):
        '''
        all tags to be created in addition to the "github-release-tag" (without the gh-release-tag)
        '''
        return self.raw.get('git_tags')[1:]

    def github_release_tag(self):
        if tags := self.raw.get('git_tags'):
            return tags[0]
        return None

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
            notification_policy=StepNotificationPolicy.NO_NOTIFICATION,
            injecting_trait_name=self.name,
            script_type=ScriptType.PYTHON3,
        )
        yield self.release_step

    def process_pipeline_args(self, pipeline_args: JobVariant):
        # we depend on all other steps
        for step in pipeline_args.steps():
            self.release_step._add_dependency(step)

        # a 'release job' should only be triggered automatically if explicitly configured
        main_repo = pipeline_args.main_repository()
        if main_repo:
            if 'trigger' not in pipeline_args.raw['repo']:
                main_repo._trigger = False

    @classmethod
    def dependencies(cls):
        return {'version', 'component_descriptor'}

    @classmethod
    def order_dependencies(cls):
        return {'publish'}
