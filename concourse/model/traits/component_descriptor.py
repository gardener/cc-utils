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

import dataclasses
import typing

import dacite

from ci.util import not_none
from gci.componentmodel import Label

from concourse.model.job import (
    JobVariant,
)
from concourse.model.step import (
    PipelineStep,
    StepNotificationPolicy,
)
from concourse.model.base import (
    AttributeSpec,
    ModelValidationError,
    ScriptType,
    Trait,
    TraitTransformer,
)
import model.ctx_repository


@dataclasses.dataclass(frozen=True)
class StepInput:
    step_name: str
    output_name: str = None # if absent, use only output
    type: str = 'step'


DEFAULT_COMPONENT_DESCRIPTOR_STEP_NAME = 'component_descriptor'

ATTRIBUTES = (
    AttributeSpec.optional(
        name='step',
        default={'name': DEFAULT_COMPONENT_DESCRIPTOR_STEP_NAME},
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
    AttributeSpec.deprecated(
        name='validation_policies',
        type=typing.List[str],
        default=['ignore-me'],
        doc='obsolete',
    ),
    AttributeSpec.deprecated(
        name='ctx_repository_base_url',
        type=str,
        default=None, # if not explicitly configured, will be injected from cicd-default
        doc='''
            the component descriptor context repository base_url (for component descriptor v2).
            If not configured, the CICD-landscape's default ctx will be used.
        '''
    ),
    AttributeSpec.optional(
        name='ctx_repository',
        type=str,
        default=None, # if not explicitly configured, will be injected from cicd-default
        doc='''
            the component descriptor context repository cfg name (for component descriptor v2).
            If not configured, the CICD-landscape's default ctx will be used.
        '''
    ),
    AttributeSpec.optional(
        name='component_labels',
        default=[],
        type=typing.List[Label],
        doc='a list of labels to add to the component in the base Component Descriptor',
    ),
    AttributeSpec.optional(
        name='inputs',
        default=[],
        type=typing.List[StepInput],
        doc='inputs to expose to component-descriptor step',
    )
)


class ComponentDescriptorTrait(Trait):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # todo: make step name actually configurable (need concept to express
        # step-specific behaviour, first)
        if not self.step_name() == DEFAULT_COMPONENT_DESCRIPTOR_STEP_NAME:
            raise ModelValidationError(
                f"component-descriptor step name must be '{DEFAULT_COMPONENT_DESCRIPTOR_STEP_NAME}'"
            )

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
        return ()

    def ctx_repository(self) -> model.ctx_repository.CtxRepositoryCfg:
        ctx_repo_name = self.raw.get('ctx_repository')
        # XXX hack for unittests
        if not self.cfg_set:
            return None
        if ctx_repo_name:
            return self.cfg_set.ctx_repository(ctx_repo_name)
        return self.cfg_set.ctx_repository()

    def ctx_repository_base_url(self):
        ctx_repo_cfg = self.ctx_repository()
        # XXX hack for unittsts
        if ctx_repo_cfg is None:
            return None

        # use default ctx_repository_base_url, if not explicitly configured
        if not (base_url := self.raw.get('ctx_repository_base_url')):
            return ctx_repo_cfg.base_url()
        else:
            # XXX warn or even forbid, at least if different from ctx-repo-cfg?
            return base_url

    def component_labels(self):
        return self.raw['component_labels']

    def inputs(self) -> typing.List[StepInput]:
        return [
            dacite.from_dict(data_class=StepInput, data=raw_input)
            for raw_input in self.raw['inputs']
        ]

    def transformer(self):
        return ComponentDescriptorTraitTransformer(trait=self)

    def validate(self):
        super().validate()
        for label in self.component_labels():
            try:
                dacite.from_dict(
                    data_class=Label,
                    data=label,
                    config=dacite.Config(strict=True),
                )
            except dacite.DaciteError as e:
                raise ModelValidationError(
                    f"Invalid label '{label}'."
                ) from e


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
            injected_by_trait=self.name,
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

        # add configured (step-)inputs
        for step_input in self.trait.inputs():
            if not step_input.type == 'step':
                raise NotImplementedError(step_input.type)

            try:
                step: PipelineStep = pipeline_args.step(step_input.step_name)
            except KeyError as ke:
                raise ValueError(f'no such step: {step_input.step_name=}') from ke

            self.descriptor_step._add_dependency(step)

            if step_input.output_name:
                output_name = step_input.output_name
            else:
                # choose only output if omitted
                outputs = {
                    name: v for name,v in step.outputs().items()
                    if not name == 'on_error_dir' # XXX hack hack hack
                }
                if len(outputs) < 1:
                    raise ValueError(f'{step.name=} does not have any outputs')
                elif len(outputs) > 1:
                    raise ValueError(
                        f'{step.name=} has more than one output (need to tell step_name)'
                    )
                output_name = next(outputs.keys().__iter__())

            self.descriptor_step.add_input(
                name=output_name,
                variable_name=output_name,
            )

    @classmethod
    def dependencies(cls):
        return {'version'}

    @classmethod
    def order_dependencies(cls):
        # dependency is required, as we need to patch the 'release' step
        return {'release'}
