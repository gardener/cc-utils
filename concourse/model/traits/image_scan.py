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

import enum
import typing

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
    ModelBase,
    ScriptType,
)
from model.base import ModelValidationError
from product.scanning import ProcessingMode
from protecode.model import CVSSVersion

import concourse.model.traits.component_descriptor
from .images import (
    IMAGE_ATTRS,
    ImageFilterMixin,
)


PROTECODE_ATTRS = (
    AttributeSpec.optional(
        name='parallel_jobs',
        default=12,
        doc='amount of parallel scanning threads',
        type=int,
    ),
    AttributeSpec.optional(
        name='cve_threshold',
        default=7,
        doc='CVE threshold to interpret as an error',
        type=int,
    ),
    AttributeSpec.optional(
        name='processing_mode',
        default=ProcessingMode.RESCAN,
        doc='Protecode processing mode',
        type=ProcessingMode,
    ),
    AttributeSpec.optional(
        name='reference_protecode_group_ids',
        default=(),
        doc='''
        an optional list of protecode group IDs to import triages from.
        ''',
    ),
    AttributeSpec.required(
        name='protecode_group_id',
        doc='technical protecode group id to upload to',
        type=int,
    ),
    AttributeSpec.optional(
        name='protecode_cfg_name',
        default=None,
        doc='protecode cfg name to use (see cc-config)',
    ),
    AttributeSpec.optional(
        name='cvss_version',
        default=CVSSVersion.V2,
        doc='CVSS version used to evaluate the severity of vulnerabilities',
        type=CVSSVersion,
    ),
)


class ProtecodeScanCfg(ModelBase):
    @classmethod
    def _attribute_specs(cls):
        return PROTECODE_ATTRS

    def reference_protecode_group_ids(self):
        return self.raw['reference_protecode_group_ids']

    def protecode_group_id(self):
        return self.raw.get('protecode_group_id')

    def protecode_cfg_name(self):
        return self.raw.get('protecode_cfg_name')

    def parallel_jobs(self):
        return self.raw.get('parallel_jobs')

    def cve_threshold(self):
        return self.raw.get('cve_threshold')

    def processing_mode(self):
        return ProcessingMode(self.raw.get('processing_mode'))

    def cvss_version(self):
        return CVSSVersion(self.raw.get('cvss_version'))

    def validate(self):
        super().validate()
        # Use enum.Enum's validation to validate configured processing mode.
        ProcessingMode(self.processing_mode())


CLAMAV_ATTRS = (
    AttributeSpec.required(
        name='clamav_cfg_name',
        doc='clamav cfg name to use (see cc-config)',
    ),
)


class ClamAVScanCfg(ModelBase):
    @classmethod
    def _attribute_specs(cls):
        return CLAMAV_ATTRS

    def clamav_cfg_name(self):
        return self.raw.get('clamav_cfg_name')

    def validate(self):
        super().validate()


class Notify(enum.Enum):
    EMAIL_RECIPIENTS = 'email_recipients'
    NOBODY = 'nobody'
    COMPONENT_OWNERS = 'component_owners'


ATTRIBUTES = (
    *IMAGE_ATTRS,
    AttributeSpec.optional(
        name='notify',
        default=Notify.EMAIL_RECIPIENTS,
        doc='whom to notify about found issues',
        type=Notify,
    ),
    AttributeSpec.required(
        name='email_recipients',
        doc='optional list of email recipients to be notified about critical scan results',
        type=typing.List[str],
    ),
    AttributeSpec.optional(
        name='protecode',
        default=None,
        type=ProtecodeScanCfg,
        doc='if present, perform protecode scanning',
    ),
    AttributeSpec.optional(
        name='clam_av',
        default=None,
        type=ClamAVScanCfg,
        doc='if present, perform ClamAV scanning',
    ),
)


class ImageScanTrait(Trait, ImageFilterMixin):
    @classmethod
    def _attribute_specs(cls):
        return ATTRIBUTES

    def _children(self):
        if self.protecode():
            yield self.protecode()
        if self.clam_av():
            yield self.clam_av()

    def notify(self):
        return Notify(self.raw['notify'])

    def email_recipients(self):
        return self.raw['email_recipients']

    def protecode(self):
        if self.raw['protecode']:
            return ProtecodeScanCfg(raw_dict=self.raw['protecode'])

    def clam_av(self):
        if self.raw['clam_av']:
            return ClamAVScanCfg(raw_dict=self.raw['clam_av'])

    def transformer(self):
        return ImageScanTraitTransformer(trait=self)

    def validate(self):
        super().validate()
        if not (self.protecode() or self.clam_av()):
            raise ModelValidationError(
                "at least one of 'protecode' or 'clam_av' must be defined."
            )


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
                notification_policy=StepNotificationPolicy.NO_NOTIFICATION,
                script_type=ScriptType.PYTHON3
        )
        self.image_scan_step.add_input(
            name=concourse.model.traits.component_descriptor.DIR_NAME,
            variable_name=concourse.model.traits.component_descriptor.ENV_VAR_NAME,
        )
        self.image_scan_step.set_timeout(duration_string='12h')
        yield self.image_scan_step

    def process_pipeline_args(self, pipeline_args: JobVariant):
        # our step depends on dependency descriptor step
        component_descriptor_step = pipeline_args.step('component_descriptor')
        self.image_scan_step._add_dependency(component_descriptor_step)

    @classmethod
    def dependencies(cls):
        return {'component_descriptor'}
