# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
# under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

from util import not_none
from model import NamedModelElement

from concourse.model.step import PipelineStep
from concourse.model.base import (
  AttributeSpec,
  Trait,
  TraitTransformer,
  ModelValidationError,
  ScriptType,
  normalise_to_dict,
)

COMPONENT_DESCRIPTOR_DIR_INPUT = ('component_descriptor_dir', 'component_descriptor_dir')

ATTRIBUTES = (
    AttributeSpec.optional(
        name='step',
        default={'name': 'component_descriptor'},
        doc='The build step name injected by this trait',
    ),
    AttributeSpec.optional(
        name='resolve_dependencies',
        default=True,
        doc='Indicates whether or not unresolved component dependencies should be resolved',
    ),
    AttributeSpec.optional(
        name='component_name',
        default=None, # actually, it is determined at runtime
        doc='Manually overwrites the component name (which defaults to github repository path)',
    ),
)


class ComponentDescriptorTrait(Trait):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # todo: make step name actually configurable (need concept to express
        # step-specific behaviour, first)
        if not self.step_name() == 'component_descriptor':
            raise ModelValidationError('component_descriptor step name must be component_descriptor')

    def _attribute_specs(self):
        return ATTRIBUTES

    def _defaults_dict(self):
        return AttributeSpec.defaults_dict(ATTRIBUTES)

    def _optional_attributes(self):
        return set(AttributeSpec.optional_attr_names(ATTRIBUTES))

    def component_name(self):
        return self.raw['component_name']

    def step_name(self):
        return self.raw['step']['name']

    def resolve_dependencies(self):
        return self.raw['resolve_dependencies']

    def transformer(self):
        return ComponentDescriptorTraitTransformer(trait=self)


class ComponentDescriptorTraitTransformer(TraitTransformer):
    name = 'component_descriptor'

    def __init__(self, trait: ComponentDescriptorTrait, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.trait = not_none(trait)

    def inject_steps(self):
        self.descriptor_step = PipelineStep(
            name=self.trait.step_name(),
            raw_dict={},
            is_synthetic=True,
            script_type=ScriptType.PYTHON3,
        )
        self.descriptor_step.add_output(*COMPONENT_DESCRIPTOR_DIR_INPUT)
        yield self.descriptor_step

    def process_pipeline_args(self, pipeline_args: 'JobVariant'):
        if pipeline_args.has_step('release'):
            release_step = pipeline_args.step('release')
            release_step.add_input(*COMPONENT_DESCRIPTOR_DIR_INPUT)

        # inject component_name if not configured
        if 'component_name' not in self.trait.raw or self.trait.raw['component_name'] is None:
            main_repo = pipeline_args.main_repository()
            component_name = '/'.join((
                main_repo.repo_hostname(),
                main_repo.repo_path(),
            ))
            self.trait.raw['component_name'] = component_name

    @classmethod
    def dependencies(cls):
        return {'version'}

    @classmethod
    def order_dependencies(cls):
        # dependency is required, as we need to patch the 'release' step
        return {'release'}
