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

from concourse.model.job import (
    JobVariant,
)
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

import concourse.model.traits.component_descriptor
from .images import (
    IMAGE_ATTRS,
    ImageFilterMixin,
)


ATTRIBUTES = (
    *IMAGE_ATTRS,
    AttributeSpec.optional(
        name='parallel_jobs',
        default=4,
        doc='how many uploads to process in parallel',
        type=int,
    ),
    AttributeSpec.required(
        name='upload_registry_prefix',
        doc='''
        all matching container images are uploaded as copies to
        the specified container registry. The original image reference names are
        mangled.
        '''
    ),
)


class ImageUploadTrait(Trait, ImageFilterMixin):
    @classmethod
    def _attribute_specs(cls):
        return ATTRIBUTES

    def _children(self):
        return ()

    def parallel_jobs(self):
        return self.raw['parallel_jobs']

    def upload_registry_prefix(self):
        return self.raw['upload_registry_prefix']

    def transformer(self):
        return ImageUploadTraitTransformer(trait=self)


class ImageUploadTraitTransformer(TraitTransformer):
    name = 'image_upload'

    def __init__(self, trait, *args, **kwargs):
        self.trait = trait
        super().__init__(*args, **kwargs)

    def inject_steps(self):
        self.image_upload_step = PipelineStep(
                name='upload_container_images',
                raw_dict={},
                is_synthetic=True,
                notification_policy=StepNotificationPolicy.NO_NOTIFICATION,
                script_type=ScriptType.PYTHON3
        )
        self.image_upload_step.add_input(
            name=concourse.model.traits.component_descriptor.DIR_NAME,
            variable_name=concourse.model.traits.component_descriptor.ENV_VAR_NAME,
        )
        self.image_upload_step.set_timeout(duration_string='12h')
        yield self.image_upload_step

    def process_pipeline_args(self, pipeline_args: JobVariant):
        # our step depends on dependency descriptor step
        component_descriptor_step = pipeline_args.step(
            concourse.model.traits.component_descriptor.DEFAULT_COMPONENT_DESCRIPTOR_STEP_NAME
        )
        self.image_upload_step._add_dependency(component_descriptor_step)

    @classmethod
    def dependencies(cls):
        return {'component_descriptor'}
