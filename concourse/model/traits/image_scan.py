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
from protecode.scanning_util import ProcessingMode

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
        default=CVSSVersion.V3,
        doc='CVSS version used to evaluate the severity of vulnerabilities',
        type=CVSSVersion,
    ),
    AttributeSpec.optional(
        name='allowed_licenses',
        default=[],
        doc=(
            'A list of regular expressions. If configured, licenses detected by protecode '
            'that do not match at least one of regular expressions will result in a report mail '
            'being sent. If not configured or empty, **all** licenses will be accepted.'
        ),
        type=list,
    ),
    AttributeSpec.optional(
        name='prohibited_licenses',
        default=[],
        doc=(
            'A list of regular expressions. If configured, licenses detected by protecode that '
            'match one of the regular expressions will result in a report mail being sent, even '
            'if they are included in `allowed_licenses` (e.g. due to the default value).'
        ),
        type=list,
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

    def allowed_licenses(self):
        return self.raw.get('allowed_licenses')

    def prohibited_licenses(self):
        return self.raw.get('prohibited_licenses')

    def validate(self):
        super().validate()
        # Use enum.Enum's validation to validate configured processing mode.
        ProcessingMode(self.processing_mode())


CLAMAV_ATTRS = (
    AttributeSpec.required(
        name='clamav_cfg_name',
        doc='clamav cfg name to use (see cc-config)',
    ),
    AttributeSpec.optional(
        name='parallel_jobs',
        doc='the amount of (maxium) parallel workers',
        type=int,
        default=8,
    ),
)


class ClamAVScanCfg(ModelBase):
    @classmethod
    def _attribute_specs(cls):
        return CLAMAV_ATTRS

    def clamav_cfg_name(self):
        return self.raw.get('clamav_cfg_name')

    def parallel_jobs(self) -> int:
        return int(self.raw['parallel_jobs'])

    def validate(self):
        super().validate()


OS_ID_SCAN_ATTRS = (
    AttributeSpec.optional(
        name='parallel_jobs',
        default=8,
        doc='amount of parallel jobs to run',
        type=int,
    ),
)


class OsIdScan(ModelBase):
    @classmethod
    def _attribute_specs(cls):
        return OS_ID_SCAN_ATTRS

    def parallel_jobs(self) -> int:
        return int(self.raw['parallel_jobs'])


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
    AttributeSpec.optional(
        name='email_recipients',
        default=[],
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
    AttributeSpec.optional(
        name='os_id',
        default=None,
        type=OsIdScan,
        doc='if present, identify operating system',
    ),
    AttributeSpec.optional(
        name='trait_depends',
        default=(),
        type=typing.Set[str],
        doc='if present, generated build steps depend on those generated from specified traits',
    )
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

    def os_id(self):
        if (raw := self.raw.get('os_id')) is not None:
            return OsIdScan(raw_dict=raw)

    def transformer(self):
        return ImageScanTraitTransformer(trait=self)

    def trait_depends(self):
        return set(self.raw['trait_depends'])

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
        if self.trait.protecode():
            self.image_scan_step = PipelineStep(
                    name='scan_container_images',
                    raw_dict={},
                    is_synthetic=True,
                    notification_policy=StepNotificationPolicy.NO_NOTIFICATION,
                    injecting_trait_name=self.name,
                    script_type=ScriptType.PYTHON3
            )
            self.image_scan_step.add_input(
                name=concourse.model.traits.component_descriptor.DIR_NAME,
                variable_name=concourse.model.traits.component_descriptor.ENV_VAR_NAME,
            )
            self.image_scan_step.set_timeout(duration_string='12h')
            yield self.image_scan_step

        if self.trait.clam_av():
            self.malware_scan_step = PipelineStep(
                    name='malware-scan',
                    raw_dict={},
                    is_synthetic=True,
                    notification_policy=StepNotificationPolicy.NO_NOTIFICATION,
                    injecting_trait_name=self.name,
                    script_type=ScriptType.PYTHON3
            )
            self.malware_scan_step.add_input(
                name=concourse.model.traits.component_descriptor.DIR_NAME,
                variable_name=concourse.model.traits.component_descriptor.ENV_VAR_NAME,
            )
            self.malware_scan_step.set_timeout(duration_string='18h')
            yield self.malware_scan_step

        if self.trait.os_id():
            self.os_id_step = PipelineStep(
                    name='os-id-scan',
                    raw_dict={},
                    is_synthetic=True,
                    notification_policy=StepNotificationPolicy.NO_NOTIFICATION,
                    injecting_trait_name=self.name,
                    script_type=ScriptType.PYTHON3
            )
            self.os_id_step.add_input(
                name=concourse.model.traits.component_descriptor.DIR_NAME,
                variable_name=concourse.model.traits.component_descriptor.ENV_VAR_NAME,
            )
            self.os_id_step.set_timeout(duration_string='2h')
            yield self.os_id_step

    def process_pipeline_args(self, pipeline_args: JobVariant):
        # our steps depends on dependency descriptor step
        component_descriptor_step = pipeline_args.step(
            concourse.model.traits.component_descriptor.DEFAULT_COMPONENT_DESCRIPTOR_STEP_NAME
        )
        if self.trait.protecode():
            self.image_scan_step._add_dependency(component_descriptor_step)
        if self.trait.clam_av():
            self.malware_scan_step._add_dependency(component_descriptor_step)
        if self.trait.os_id():
            self.os_id_step._add_dependency(component_descriptor_step)

        for trait_name in self.trait.trait_depends():
            if not pipeline_args.has_trait(trait_name):
                raise ModelValidationError(f'dependency towards absent trait: {trait_name}')

            depended_on_trait = pipeline_args.trait(trait_name)
            # XXX refactor Trait/TraitTransformer
            transformer = depended_on_trait.transformer()
            # XXX step-injection may have (unintended) side-effects :-/
            depended_on_step_names = {step.name for step in transformer.inject_steps()}

            for step in pipeline_args.steps():
                if not step.name in depended_on_step_names:
                    continue
                if self.trait.protecode():
                    self.image_scan_step._add_dependency(step)
                    # prevent cyclic dependencies (from auto-injected depends)
                    if self.image_scan_step.name in step.depends():
                        step._remove_dependency(self.image_scan_step)

                if self.trait.clam_av():
                    self.malware_scan_step._add_dependency(step)
                    # prevent cyclic dependencies (from auto-injected depends)
                    if self.malware_scan_step.name in step.depends():
                        step._remove_dependency(self.malware_scan_step)

    @classmethod
    def dependencies(cls):
        return {'component_descriptor'}

    @classmethod
    def order_dependencies(cls):
        # required in case image-scanning should be done after publish
        # (-> auto-injected dependency for "prepare"-step towards _all_ steps)
        return {'publish'}
