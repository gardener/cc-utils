# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

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
        component_descriptor_step = pipeline_args.step('component_descriptor')
        self.image_upload_step._add_dependency(component_descriptor_step)

    @classmethod
    def dependencies(cls):
        return {'component_descriptor'}
