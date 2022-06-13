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

import enum
import typing

from concourse.model.step import (
    PipelineStep,
    PrivilegeMode,
    PullRequestNotificationPolicy,
)
from concourse.model.base import (
    AttributeSpec,
    ModelBase,
    ScriptType,
    Trait,
    TraitTransformer,
)
from concourse.model.job import (
    JobVariant,
)
from model.base import ModelValidationError

import concourse.model.traits.component_descriptor
import concourse.model.traits.images

OciImageCfg = concourse.model.traits.images.OciImageCfg


class MergePolicy(enum.Enum):
    MANUAL = 'manual'
    AUTO_MERGE = 'auto_merge'


class MergeMethod(enum.Enum):
    MERGE = 'merge'
    REBASE = 'rebase'
    SQUASH = 'squash'


class UpstreamUpdatePolicy(enum.Enum):
    STRICTLY_FOLLOW = 'strictly_follow'
    ACCEPT_HOTFIXES = 'accept_hotfixes'


MERGE_POLICY_CONFIG_ATTRIBUTES = (
    AttributeSpec.optional(
        name='component_names',
        default=[],
        type=typing.List[str],
        doc=(
            'a sequence of regular expressions. This merge policy will be applied to matching '
            'component names. Matches all component names by default'
        )
    ),
    AttributeSpec.optional(
        name='merge_mode',
        default='manual',
        type=MergePolicy,
        doc='whether or not created PRs should be automatically merged',
    ),
    AttributeSpec.optional(
        name='merge_method',
        default='merge',
        type=MergeMethod,
        doc=(
            'The method to use when merging PRs automatically. For more details, check '
            '`the GitHub documentation for merge-methods <https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/about-merge-methods-on-github>`_.' # noqa:E501
        )
    ),
)


class MergePolicyConfig(ModelBase):
    @classmethod
    def _attribute_specs(cls):
        return MERGE_POLICY_CONFIG_ATTRIBUTES

    def component_names(self):
        # handle default here
        # TODO: refactor default arg handling. User-given values should *replace* defaults, not
        #       add to them
        if not self.raw['component_names']:
            return ['.*']

        return self.raw['component_names']

    def merge_mode(self):
        return MergePolicy(self.raw['merge_mode'])

    def merge_method(self):
        return MergeMethod(self.raw['merge_method'])


ATTRIBUTES = (
    AttributeSpec.optional(
        name='set_dependency_version_script',
        default='.ci/set_dependency_version',
        doc='configures the path to set_dependency_version script',
    ),
    AttributeSpec.optional(
        name='set_dependency_version_script_container_image',
        default=None,
        doc='''
            if specified, the `set_dependency_version_script` will be executed in a separate
            OCI container using the specified container image.
            main_repository will be mounted at /mnt/main_repo
        ''',
        type=concourse.model.traits.images.OciImageCfg,
    ),
    AttributeSpec.optional(
        name='upstream_component_name',
        default=None, # defaults to main repository
        doc='configures the upstream component',
    ),
    AttributeSpec.optional(
        name='upstream_update_policy',
        default=UpstreamUpdatePolicy.STRICTLY_FOLLOW,
        doc='configures the upstream component update policy',
    ),
    AttributeSpec.deprecated(
        name='merge_policy',
        default=None,
        doc='whether or not created PRs should be automatically merged. **deprecated**',
        type=MergePolicy,
    ),
    AttributeSpec.optional(
        name='merge_policies',
        default=(),
        doc=(
            'merge policies to apply to detected component upgrades. By default, upgrade '
            'pull-requests must be merged manually'
        ),
        type=typing.List[MergePolicyConfig],
    ),
    AttributeSpec.optional(
        name='after_merge_callback',
        default=None,
        doc='callback to be invoked after auto-merge',
    ),
    AttributeSpec.optional(
        name='ignore_prerelease_versions',
        default=True,
        doc=(
            'ignores prerelease versions like "0.1.0-dev-abc" and '
            'only creates upgrade pr\'s for finalized versions.'
        ),
        type=bool,
    ),
    AttributeSpec.optional(
        name='vars',
        default={},
        doc='env vars to pass to after_merge_callback (similar to step\'s vars)',
        type=dict,
    )
)


class UpdateComponentDependenciesTrait(Trait):
    @classmethod
    def _attribute_specs(cls):
        return ATTRIBUTES

    def set_dependency_version_script_path(self):
        return self.raw['set_dependency_version_script']

    def set_dependency_version_script_container_image(self) -> typing.Optional[OciImageCfg]:
        if raw := self.raw.get('set_dependency_version_script_container_image'):
            return OciImageCfg(raw_dict=raw)
        else:
            return None

    def upstream_component_name(self):
        return self.raw.get('upstream_component_name')

    def upstream_update_policy(self):
        return UpstreamUpdatePolicy(self.raw.get('upstream_update_policy'))

    def merge_policies(self):
        # handle default here
        # TODO: refactor default arg handling. User-given values should *replace* defaults, not
        #       add to them
        if not self.raw.get('merge_policies'):
            if not self.raw.get('merge_policy'):
                return [
                    MergePolicyConfig({
                        'component_names': ['.*'],
                        'merge_mode': 'manual',
                        'merge_method': 'merge',
                    })]

            # preserve legacy behaviour
            # TODO rm
            else:
                return [
                    MergePolicyConfig({
                        'component_names': ['.*'],
                        'merge_mode': self.raw['merge_policy'],
                        'merge_method': 'merge',
                    })
                ]

        else:
            return [
                MergePolicyConfig(cfg) for cfg in self.raw['merge_policies']
            ]

    def after_merge_callback(self):
        return self.raw.get('after_merge_callback')

    def ignore_prerelease_versions(self):
        return self.raw.get('ignore_prerelease_versions')

    def vars(self):
        return self.raw['vars']

    def transformer(self):
        return UpdateComponentDependenciesTraitTransformer(trait=self)

    def validate(self):
        super().validate()
        if self.raw.get('merge_policy') and self.raw.get('merge_policies'):
            raise ModelValidationError(
                "Only one of 'merge_policy' and 'merge_policies' is allowed."
            )
        for config in self.merge_policies():
            config.validate()


class UpdateComponentDependenciesTraitTransformer(TraitTransformer):
    name = 'update_component_deps'

    def __init__(self, trait, *args, **kwargs):
        self.trait = trait
        super().__init__(*args, **kwargs)

    @classmethod
    def order_dependencies(cls):
        return {'component_descriptor'}

    @classmethod
    def dependencies(cls):
        return {'component_descriptor'}

    def inject_steps(self):
        if self.trait.set_dependency_version_script_container_image():
            privilege_mode = PrivilegeMode.PRIVILEGED
        else:
            privilege_mode = PrivilegeMode.UNPRIVILEGED

        # declare no dependencies --> run asap, but do not block other steps
        self.update_component_deps_step = PipelineStep(
                name='update_component_dependencies',
                raw_dict={
                    'privilege_mode': privilege_mode,
                },
                is_synthetic=True,
                pull_request_notification_policy=PullRequestNotificationPolicy.NO_NOTIFICATION,
                injecting_trait_name=self.name,
                script_type=ScriptType.PYTHON3
        )
        self.update_component_deps_step.add_input(
            name=concourse.model.traits.component_descriptor.DIR_NAME,
            variable_name=concourse.model.traits.component_descriptor.ENV_VAR_NAME,
        )
        self.update_component_deps_step.set_timeout(duration_string='30m')

        for name, value in self.trait.vars().items():
            self.update_component_deps_step.variables()[name] = value

        yield self.update_component_deps_step

    def process_pipeline_args(self, pipeline_args: JobVariant):
        # our step depends on dependendency descriptor step
        component_descriptor_step = pipeline_args.step(
            concourse.model.traits.component_descriptor.DEFAULT_COMPONENT_DESCRIPTOR_STEP_NAME
        )
        self.update_component_deps_step._add_dependency(component_descriptor_step)

        upstream_component_name = self.trait.upstream_component_name()
        if upstream_component_name:
            self.update_component_deps_step.variables()['UPSTREAM_COMPONENT_NAME'] = '"{cn}"'.format(
                cn=upstream_component_name,
            )
