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
import enum
import textwrap
import typing

import dacite

from ci.util import not_none
from gci.componentmodel import Label
import ci.util
import cnudie.util
import gci.componentmodel as cm
import version

from concourse.model.job import (
    JobVariant,
)
from concourse.model.step import (
    PipelineStep,
    PullRequestNotificationPolicy,
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


class UploadMode(enum.StrEnum):
    LEGACY = 'legacy'
    NO_UPLOAD = 'no-upload'


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
        name='upload',
        default='legacy',
        doc=textwrap.dedent('''\
            Indicates whether or not to publish component-descriptor during Component-Descriptor
            step. For backwards-compatibility reasons, defaults to "legacy", which is a mode
            that depends on pipeline's context.
            Should be configured to `no-upload` (which is the intended default behaviour)
        '''),
        type=UploadMode,
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
        name='retention_policy',
        default=None,
        type=typing.Union[version.VersionRetentionPolicies, str],
        doc='''
            specifies how (old) component-descriptors and their referenced resources should be
            handled. This is foremostly intended as an option for automated cleanup for
            components with frequent (shortlived) releases and/or frequent (shortlived) snapshots.

            if no retention_policy is defined, no cleanup will be done.

            retention policy may either be defined "inline" (as a mapping value) or by referencing
            a pre-defined policy by name (see `retentions_policies` attribute). In the latter case,
            use the policie's name as (string) attribute value.
        ''',
    ),
    AttributeSpec.optional(
        name='retention_policies',
        default=[
            version.VersionRetentionPolicies(
                name='clean-snapshots',
                rules=[
                    version.VersionRetentionPolicy(
                        name='clean-snapshots',
                        keep=64,
                        match=version.VersionType.SNAPSHOT,
                    ),
                    version.VersionRetentionPolicy(
                        name='keep-releases',
                        keep='all',
                        match=version.VersionType.RELEASE,
                    ),
                ],
                dry_run=False,
            ),
            version.VersionRetentionPolicies(
                name='clean-snapshots-and-releases',
                rules=[
                    version.VersionRetentionPolicy(
                        name='clean-snapshots',
                        keep=64,
                        match=version.VersionType.SNAPSHOT,
                    ),
                    version.VersionRetentionPolicy(
                        name='clean-releases',
                        keep=128,
                        match=version.VersionType.RELEASE,
                    ),
                ],
                dry_run=False,
            ),
        ],
        type=typing.List[version.VersionRetentionPolicies],
        doc='''
            predefined retention policies (see default value). may be referenced via
            `retention_policy` attribute (adding additional policies here has no immediate effect)
        '''
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
        name='snapshot_ctx_repository',
        type=str,
        default=None, # if not explicitly configured, will be injected from cicd-default
        doc='''
            the component descriptor context repository cfg name for snapshot-component-descriptors,
            i.e. component descriptors that are not for release-versions.
            If not configured, the CICD_landscape's default ctx will be used.
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
    ),
    AttributeSpec.optional(
        name='ocm_repository_mappings',
        default=[], # cannot define a proper default here because this depends on another (optional)
                    # config-value. At least not in a way that would be represented in our
                    # rendered documentation.
        type=typing.List[cnudie.util.OcmResolverConfig],
        doc='''
            used to explicitly configure where to lookup component descriptors. Example:

            .. code-block:: yaml

                - repository: ocm_repo_url
                  prefix: github.com/some-org/
                - repository: ocm_repo_url
                  prefix: github.com/another-org/
                  priority: 10 # default
                - repository: another_ocm_repo_url
                  prefix: github.com/yet-another-org/

            If not given, a default mapping will be applied that is equivalent to the following:

            .. code-block:: yaml

                - repository: <ctx_repository_base_url>
                  prefix: ''
                  priority: 10

            .. note::
                If multiple mappings match a component name, they will be tried in order of priority
                with longest matching prefix first.

        '''
    ),
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

    @property
    def upload(self) -> UploadMode:
        return UploadMode(self.raw['upload'])

    def retention_policy(self, raw=True) -> version.VersionRetentionPolicies | None:
        if not (policy := self.raw.get('retention_policy', None)):
            return None

        if isinstance(policy, str):
            # lookup name
            for candidate in self.raw.get('retention_policies', ()):
                if isinstance(candidate, dict):
                    name = candidate['name']
                elif isinstance(candidate, version.VersionRetentionPolicies):
                    name = candidate.name
                else:
                    raise ValueError(candidate)

                if name == policy:
                    policy = candidate
                    break
            else:
                raise ValueError(f'did not find {policy=} in retention_policies')

        if raw:
            if isinstance(policy, version.VersionRetentionPolicies):
                policy = dataclasses.asdict(
                    policy,
                    dict_factory=ci.util.dict_factory_enum_serialisiation,
                )
            return policy

        if isinstance(policy, version.VersionRetentionPolicies):
            return policy

        return dacite.from_dict(
            data_class=version.VersionRetentionPolicies,
            data=policy,
            config=dacite.Config(
                cast=(enum.Enum,),
            ),
        )

    def resolve_dependencies(self):
        return self.raw['resolve_dependencies']

    def callback_env(self) -> dict:
        return self.raw['callback_env']

    def validation_policies(self):
        return ()

    def snapshot_ctx_repository(self):
        # work around unittests
        if not self.cfg_set:
            return None

        if snapshot_ctx_repo_name := self.raw['snapshot_ctx_repository']:
            return self.cfg_set.ctx_repository(snapshot_ctx_repo_name)
        else:
            return self.cfg_set.ctx_repository()

    def snapshot_ctx_repository_base_url(self):
        if snapshot_repo_cfg := self.snapshot_ctx_repository():
            return snapshot_repo_cfg.base_url()

    def ctx_repository(self) -> cm.OciRepositoryContext:
        ctx_repo_name = self.raw.get('ctx_repository')
        # XXX hack for unittests
        if not self.cfg_set:
            return None
        if ctx_repo_name:
            ctx_repo_cfg = self.cfg_set.ctx_repository(ctx_repo_name)
        else:
            ctx_repo_cfg = self.cfg_set.ctx_repository()

        ctx_repo_cfg: model.ctx_repository.CtxRepositoryCfg

        return cm.OciRepositoryContext(
            baseUrl=ctx_repo_cfg.base_url(),
        )

    def ctx_repository_base_url(self):
        ctx_repo = self.ctx_repository()
        # XXX hack for unittsts
        if ctx_repo is None:
            return None

        # use default ctx_repository_base_url, if not explicitly configured
        if not (base_url := self.raw.get('ctx_repository_base_url')):
            return ctx_repo.baseUrl
        else:
            # XXX warn or even forbid, at least if different from ctx-repo-cfg?
            return base_url

    def component_labels(self):
        return self.raw['component_labels']

    def ocm_repository_mappings(self) -> list:
        ctx_repository_url = self.ctx_repository_base_url()
        if ctx_repository_url is None:
            return []
        if not (ocm_repository_mappings := self.raw['ocm_repository_mappings']):
            ocm_repository_mappings = [{
                'repository': ctx_repository_url,
                'prefix': '',
                'priority': 10,

            }]

        return ocm_repository_mappings

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
            pull_request_notification_policy=PullRequestNotificationPolicy.NO_NOTIFICATION,
            injecting_trait_name=self.name,
            script_type=ScriptType.PYTHON3,
        )
        self.descriptor_step.add_output(
            name=DIR_NAME,
            variable_name=ENV_VAR_NAME,
        )
        self.descriptor_step.set_timeout(duration_string='45m')

        yield self.descriptor_step

    def process_pipeline_args(self, pipeline_args: 'JobVariant'):
        if pipeline_args.has_step('release'):
            release_step = pipeline_args.step('release')
            release_step.add_input(
                name=DIR_NAME,
                variable_name=ENV_VAR_NAME,
            )
        if pipeline_args.has_trait('draft_release'):
            draft_release_step = pipeline_args.step('create_draft_release_notes')
            draft_release_step.add_input(
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
