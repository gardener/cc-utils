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
    ModelBase,
    Trait,
    TraitTransformer,
    ScriptType,
)
import concourse.model.traits.component_descriptor


class Notify(enum.Enum):
    EMAIL_RECIPIENTS = 'email_recipients'
    NOBODY = 'nobody'
    COMPONENT_OWNERS = 'component_owners'


CHECKMARX_ATTRIBUTES = (
    AttributeSpec.required(
        name='team_id',
        doc='checkmarx team id',
        type=int,
    ),
    AttributeSpec.optional(
        name='severity_threshold',
        default=30,
        doc='threshold above which to notify recipients',
        type=int,
    ),
    AttributeSpec.required(
        name='cfg_name',
        doc='config name for checkmarx',
        type=str,
    ),
)


class CheckmarxCfg(ModelBase):
    @classmethod
    def _attribute_specs(cls):
        return CHECKMARX_ATTRIBUTES

    def team_id(self):
        return self.raw['team_id']

    def severity_threshold(self) -> int:
        return int(self.raw.get('severity_threshold'))

    def checkmarx_cfg_name(self):
        return self.raw.get('cfg_name')


ATTRIBUTES = (
    AttributeSpec.optional(
        name='notify',
        default=Notify.EMAIL_RECIPIENTS,
        doc='whom to notify about found issues',
        type=Notify,
    ),
    AttributeSpec.optional(
        name='email_recipients',
        default=(),
        doc='optional list of email recipients to be notified about critical scan results',
        type=typing.List[str],
    ),
    AttributeSpec.required(
        name='checkmarx',
        type=CheckmarxCfg,
        doc='if present, perform checkmarx scanning',
    ),
)


class SourceScanTrait(Trait):
    @classmethod
    def _attribute_specs(cls):
        return ATTRIBUTES

    def _children(self):
        if self.checkmarx():
            yield self.checkmarx()

    def notify(self):
        return Notify(self.raw['notify'])

    def email_recipients(self):
        return self.raw['email_recipients']

    def checkmarx(self):
        if checkmarx := self.raw.get('checkmarx'):
            return CheckmarxCfg(checkmarx)

    def transformer(self):
        return SourceScanTraitTransformer(trait=self)


class SourceScanTraitTransformer(TraitTransformer):
    name = 'scan_sources'

    def __init__(self, trait, *args, **kwargs):
        self.trait = trait
        super().__init__(*args, **kwargs)

    def inject_steps(self):
        self.source_scan_step = PipelineStep(
            name='scan_sources',
            raw_dict={},
            is_synthetic=True,
            notification_policy=StepNotificationPolicy.NO_NOTIFICATION,
            script_type=ScriptType.PYTHON3
        )
        self.source_scan_step.add_input(
            name=concourse.model.traits.component_descriptor.DIR_NAME,
            variable_name=concourse.model.traits.component_descriptor.ENV_VAR_NAME,
        )
        self.source_scan_step.set_timeout(duration_string='18h')
        yield self.source_scan_step

    def process_pipeline_args(self, pipeline_args: JobVariant):
        # our step depends on dependency descriptor step
        component_descriptor_step = pipeline_args.step('component_descriptor')
        self.source_scan_step._add_dependency(component_descriptor_step)

    @classmethod
    def dependencies(cls):
        return {'component_descriptor'}
