# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

from util import ensure_not_none
from model import NamedModelElement

from concourse.pipelines.model.step import PipelineStep
from concourse.pipelines.modelbase import (
  Trait,
  TraitTransformer,
  ModelValidationError,
  normalise_to_dict,
)


class ComponentDescriptorTrait(Trait):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # init defaults
        if not 'step' in self.raw:
            self.raw['step'] = {}
        step = self.raw['step']
        if not 'name' in step:
            step['name'] = 'component_descriptor'

        # todo: make step name actually configurable (need concept to express
        # step-specific behaviour, first)
        if not step['name'] == 'component_descriptor':
            raise ModelValidationError('component_descriptor step name must be component_descriptor')

    def component_name(self):
        return self.raw['component_name']

    def step_name(self):
        return self.raw['step']['name']

    def resolve_dependencies(self):
        return self.raw.get('resolve_dependencies', True)

    def transformer(self):
        return ComponentDescriptorTraitTransformer(trait=self, name=self.name)


class ComponentDescriptorTraitTransformer(TraitTransformer):
    def __init__(self, trait: ComponentDescriptorTrait, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.trait = ensure_not_none(trait)

    def inject_steps(self):
        self.descriptor_step = PipelineStep(
            name=self.trait.step_name(),
            raw_dict={},
            is_synthetic=True
        )
        self.descriptor_step.add_output('component_descriptor_dir', 'component_descriptor_dir')
        yield self.descriptor_step

    def process_pipeline_args(self, pipeline_args: 'PipelineArgs'):
        if pipeline_args.has_step('release'):
            release_step = pipeline_args.step('release')
            release_step.add_input('component_descriptor_dir', 'component_descriptor_dir')

    def dependencies(self):
        # dependency is required, as we need to patch the 'release' step
        return super().dependencies() | {'release'}

