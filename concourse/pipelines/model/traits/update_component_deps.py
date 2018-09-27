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

from concourse.pipelines.model.step import PipelineStep
from concourse.pipelines.model.base import Trait, TraitTransformer, ModelBase, ScriptType

from .component_descriptor import COMPONENT_DESCRIPTOR_DIR_INPUT


class UpdateComponentDependenciesTrait(Trait):
    def _defaults_dict(self):
        return {
            'set_dependency_version_script': '.ci/set_dependency_version',
        }

    def _optional_attributes(self):
        return {
            'upstream_component_name',
        }

    def set_dependency_version_script_path(self):
        return self.raw['set_dependency_version_script']

    def upstream_component_name(self):
        return self.raw.get('upstream_component_name')

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
                script_type=ScriptType.PYTHON3
        )
        self.update_component_deps_step.add_input(*COMPONENT_DESCRIPTOR_DIR_INPUT)
        yield self.update_component_deps_step

    def process_pipeline_args(self, pipeline_args: 'JobVariant'):
        # our step depends on dependendency descriptor step
        component_descriptor_step = pipeline_args.step('component_descriptor')
        self.update_component_deps_step._add_dependency(component_descriptor_step)

        upstream_component_name = self.trait.upstream_component_name()
        if upstream_component_name:
            self.update_component_deps_step.variables()['UPSTREAM_COMPONENT_NAME'] = '"{cn}"'.format(
                cn=upstream_component_name,
            )
