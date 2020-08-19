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

from ci.util import not_none

from concourse.model.job import (
    JobVariant,
)
from concourse.model.step import (
    PipelineStep,
    StepNotificationPolicy,
)
from concourse.model.base import (
    AttribSpecMixin,
    AttributeSpec,
    ModelValidationError,
    ScriptType,
    Trait,
    TraitTransformer,
)


class ValidationPolicy(AttribSpecMixin, enum.Enum):
    NOT_EMPTY = "not_empty"
    FORBID_EXTRA_ATTRIBUTES = "forbid_extra_attributes"

    def __str__(self):
        return self.value

    @classmethod
    def _attribute_specs(cls):
        return (
            AttributeSpec.optional(
                name=cls.NOT_EMPTY.value,
                default=None,
                doc=(
                    'Every given attribute (e.g.: "version") must also be given a '
                    'non-empty value'
                ),
                type=str,
            ),
            AttributeSpec.optional(
                name=cls.FORBID_EXTRA_ATTRIBUTES.value,
                default=None,
                doc='**only** required attributes are allowed',
                type=str,
            ),
        )


ATTRIBUTES = (
    AttributeSpec.optional(
        name='step',
        default={'name': 'component_descriptor'},
        doc='The build step name injected by this trait',
        type=dict,
    ),
    AttributeSpec.optional(
        name='resolve_dependencies',
        default=True,
        doc='Indicates whether or not unresolved component dependencies should be resolved',
        type=bool,
    ),
    AttributeSpec.optional(
        name='component_name',
        default=None, # actually, it is determined at runtime
        doc='Manually overwrites the component name (which defaults to github repository path)',
    ),
    AttributeSpec.optional(
        name='callback_env',
        default={},
        doc='Specifies additional environment variables passed to .ci/component_descriptor script',
    ),
    AttributeSpec.optional(
        name='validation_policies',
        type=typing.List[ValidationPolicy],
        default=[ValidationPolicy.FORBID_EXTRA_ATTRIBUTES],
        doc=(
            'The validation policies that should be applied to arguments of components added to '
            'the component descriptor'
        ),
    ),
    AttributeSpec.optional(
        name='ctx_repository_base_url',
        type=str,
        default=None, # if not explicitly configured, will be injected from cicd-default
        doc='''
            the component descriptor context repository base_url (for component descriptor v2).
            If not configured, the CICD-landscape's default ctx will be used.
        '''
    ),
)


class ComponentDescriptorTrait(Trait):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # todo: make step name actually configurable (need concept to express
        # step-specific behaviour, first)
        if not self.step_name() == 'component_descriptor':
            raise ModelValidationError('component_descriptor step name must be component_descriptor')

    @classmethod
    def _attribute_specs(cls):
        return ATTRIBUTES

    def component_name(self):
        return self.raw['component_name']

    def step_name(self):
        return self.raw['step']['name']

    def resolve_dependencies(self):
        return self.raw['resolve_dependencies']

    def callback_env(self) -> dict:
        return self.raw['callback_env']

    def validation_policies(self):
        return [
            ValidationPolicy(v)
            for v in self.raw['validation_policies']
        ]

    def ctx_repository_base_url(self):
        # use default ctx_repository_base_url, if not explicitly configured
        if not (base_url := self.raw.get('ctx_repository_base_url')):
            if not self.cfg_set:
                return None
            ctx_repo_cfg = self.cfg_set.ctx_repository()
            base_url = ctx_repo_cfg.base_url()
            self.raw['ctx_repository_base_url'] = base_url
        return base_url

    def transformer(self):
        return ComponentDescriptorTraitTransformer(trait=self)


DIR_NAME = 'component_descriptor_dir'
ENV_VAR_NAME = 'component_descriptor_dir'


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
            notification_policy=StepNotificationPolicy.NO_NOTIFICATION,
            script_type=ScriptType.PYTHON3,
        )
        self.descriptor_step.add_output(
            name=DIR_NAME,
            variable_name=ENV_VAR_NAME,
        )
        self.descriptor_step.set_timeout(duration_string='30m')

        yield self.descriptor_step

    def process_pipeline_args(self, pipeline_args: 'JobVariant'):
        if pipeline_args.has_step('release'):
            release_step = pipeline_args.step('release')
            release_step.add_input(
                name=DIR_NAME,
                variable_name=ENV_VAR_NAME,
            )

        # inject component_name if not configured
        if not self.trait.raw.get('component_name'):
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
