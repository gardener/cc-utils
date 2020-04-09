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

from concourse.model.step import (
    PipelineStep,
    StepNotificationPolicy,
)
from concourse.model.base import (
    AttributeSpec,
    Trait,
    TraitTransformer,
    ScriptType,
)
from concourse.model.job import (
    JobVariant,
)

import concourse.model.traits.component_descriptor


class MergePolicy(enum.Enum):
    MANUAL = 'manual'
    AUTO_MERGE = 'auto_merge'


class UpstreamUpdatePolicy(enum.Enum):
    STRICTLY_FOLLOW = 'strictly_follow'
    ACCEPT_HOTFIXES = 'accept_hotfixes'


ATTRIBUTES = (
    AttributeSpec.optional(
        name='set_dependency_version_script',
        default='.ci/set_dependency_version',
        doc='configures the path to set_dependency_version script',
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
    AttributeSpec.optional(
        name='merge_policy',
        default=MergePolicy.MANUAL,
        doc='whether or not created PRs should be automatically merged',
    ),
    AttributeSpec.optional(
        name='after_merge_callback',
        default=None,
        doc='callback to be invoked after auto-merge',
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

    def upstream_component_name(self):
        return self.raw.get('upstream_component_name')

    def upstream_update_policy(self):
        return UpstreamUpdatePolicy(self.raw.get('upstream_update_policy'))

    def merge_policy(self) -> MergePolicy:
        return MergePolicy(self.raw['merge_policy'])

    def after_merge_callback(self):
        return self.raw.get('after_merge_callback')

    def vars(self):
        return self.raw['vars']

    def transformer(self):
        return UpdateComponentDependenciesTraitTransformer(trait=self)


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
        # declare no dependencies --> run asap, but do not block other steps
        self.update_component_deps_step = PipelineStep(
                name='update_component_dependencies',
                raw_dict={},
                is_synthetic=True,
                notification_policy=StepNotificationPolicy.NO_NOTIFICATION,
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
        component_descriptor_step = pipeline_args.step('component_descriptor')
        self.update_component_deps_step._add_dependency(component_descriptor_step)

        upstream_component_name = self.trait.upstream_component_name()
        if upstream_component_name:
            self.update_component_deps_step.variables()['UPSTREAM_COMPONENT_NAME'] = '"{cn}"'.format(
                cn=upstream_component_name,
            )
