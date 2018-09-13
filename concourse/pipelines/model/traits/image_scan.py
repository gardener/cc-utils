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
from concourse.pipelines.modelbase import (
    Trait,
    TraitTransformer,
    ModelBase,
    ScriptType,
)

from .component_descriptor import COMPONENT_DESCRIPTOR_DIR_INPUT


class ImageScanTrait(Trait):
    def _defaults_dict(self):
        return {
            'parallel_jobs': 12,
            'cve_threshold': 7,
        }

    def _required_attributes(self):
        return (
            'protecode_group_id',
            'protecode_cfg_name',
        )

    def protecode_group_id(self):
        return self.raw.get('protecode_group_id')

    def protecode_cfg_name(self):
        return self.raw.get('protecode_cfg_name')

    def parallel_jobs(self):
        return self.raw.get('parallel_jobs')

    def cve_threshold(self):
        return self.raw.get('cve_threshold')

    def transformer(self):
        return ImageScanTraitTransformer(trait=self)


class ImageScanTraitTransformer(TraitTransformer):
    name = 'image_scan'

    def __init__(self, trait, *args, **kwargs):
        self.trait = trait
        super().__init__(*args, **kwargs)

    def inject_steps(self):
        self.image_scan_step = PipelineStep(
                name='scan_container_images',
                raw_dict={},
                is_synthetic=True,
                script_type=ScriptType.PYTHON3
        )
        self.image_scan_step.add_input(*COMPONENT_DESCRIPTOR_DIR_INPUT)
        yield self.image_scan_step

    def process_pipeline_args(self, pipeline_args: 'JobVariant'):
        # our step depends on dependency descriptor step
        component_descriptor_step = pipeline_args.step('component_descriptor')
        self.image_scan_step._add_dependency(component_descriptor_step)

    @classmethod
    def dependencies(cls):
        return super().dependencies() | {'component_descriptor'}
