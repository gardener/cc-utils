# Copyright (c) 2019 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
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

import util

from concourse.model.step import PipelineStep
from concourse.model.base import (
    AttributeSpec,
    Trait,
    TraitTransformer,
    ModelBase,
    ScriptType,
)

from .component_descriptor import COMPONENT_DESCRIPTOR_DIR_INPUT

ATTRIBUTES = (
    AttributeSpec.optional(
        name='parallel_jobs',
        default=12,
        doc='amount of parallel scanning threads',
        type=int,
    ),
    AttributeSpec.required(
        name='cfgs',
        doc='ImageAlterCfgs {name: ImageAlterCfg}',
        type=dict, # TODO: define types
    ),
)

IMG_ALTER_ATTRS = (
    AttributeSpec.required(
        name='src_ref',
        doc='source image reference (including tag)',
    ),
    AttributeSpec.required(
        name='tgt_ref',
        doc='target image reference (tag defaults to src_ref tag if absent)',
    ),
    AttributeSpec.required(
        name='remove_paths_file',
        doc='''
        path to a text file containing absolute paths (w/o leading /) to be purged from
        src image. Interpreted relative to main repository root.
        '''
    ),
)


class ImageAlterCfg(ModelBase):
    def __init__(
        self,
        name: str,
        raw_dict,
        *args,
        **kwargs,
    ):
        self._name = name
        super().__init__(
            raw_dict=raw_dict,
            *args,
            **kwargs,
        )

    def _attribute_specs(self):
        return IMG_ALTER_ATTRS

    def _defaults_dict(self):
        return AttributeSpec.defaults_dict(IMG_ALTER_ATTRS)

    def _optional_attributes(self):
        return set(AttributeSpec.optional_attr_names(IMG_ALTER_ATTRS))

    def _required_attributes(self):
        return set(AttributeSpec.required_attr_names(IMG_ALTER_ATTRS))

    def name(self):
        return self._name

    def src_ref(self):
        ref = self.raw['src_ref']
        # very basic poor-man's validation
        if ':' not in ref:
            raise ValueError(f'img-ref must contain tag: {ref}')
        return ref

    def tgt_ref(self):
        # XXX validate ref schema
        ref = self.raw['tgt_ref']
        if ':' in ref:
            return ref
        # cp tag from src_ref
        _, tag = self.src_ref().rsplit(':', 1)
        return f'{ref}:{tag}'

    def rm_paths_file(self):
        return self.raw['remove_paths_file']


class ImageAlterTrait(Trait):
    def _attribute_specs(self):
        return ATTRIBUTES

    def _defaults_dict(self):
        return AttributeSpec.defaults_dict(ATTRIBUTES)

    def _optional_attributes(self):
        return set(AttributeSpec.optional_attr_names(ATTRIBUTES))

    def _required_attributes(self):
        return set(AttributeSpec.required_attr_names(ATTRIBUTES))

    def image_alter_cfgs(self):
        return (
            ImageAlterCfg(name=name, raw_dict=raw)
            for name, raw in self.raw['cfgs'].items()
        )

    def transformer(self):
        return ImageAlterTraitTransformer(trait=self)


class ImageAlterTraitTransformer(TraitTransformer):
    name = 'image_alter'

    def __init__(self, trait, *args, **kwargs):
        self.trait = trait
        super().__init__(*args, **kwargs)

    def inject_steps(self):
        self.image_alter_step = PipelineStep(
                name='alter_container_images',
                raw_dict={},
                is_synthetic=True,
                script_type=ScriptType.PYTHON3
        )
        yield self.image_alter_step

    def process_pipeline_args(self, pipeline_args: 'JobVariant'):
        pass

    @classmethod
    def dependencies(cls):
        return {'component_descriptor'}
