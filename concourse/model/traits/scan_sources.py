import typing

import ci.util
import dacite

from concourse.model.job import (
    JobVariant,
)
from concourse.model.step import (
    PipelineStep,
    PullRequestNotificationPolicy,
)
from concourse.model.base import (
    AttributeSpec,
    ModelBase,
    Trait,
    TraitTransformer,
    ScriptType,
)
import concourse.model.traits.component_descriptor

from concourse.model.traits.image_scan import (
    GithubIssueTemplateCfg,
    IssuePolicies,
    Notify,
)


CHECKMARX_ATTRIBUTES = (
    AttributeSpec.required(
        name='team_id',
        doc='checkmarx team id',
        type=int,
    ),
    AttributeSpec.optional(
        name='severity_threshold',
        default='medium',
        doc='threshold for creating issues (high, medium, low, info)',
        type=str,
    ),
    AttributeSpec.required(
        name='cfg_name',
        doc='config name for checkmarx',
        type=str,
    ),
    AttributeSpec.optional(
        name='include_path_regexes',
        doc='paths which should be included in the scan',
        default=(),
        type=typing.List[str],
    ),
    AttributeSpec.optional(
        doc='paths which should be excluded in the scan',
        default=(),
        name='exclude_path_regexes',
        type=typing.List[str],
    ),
    AttributeSpec.optional(
        name='scan_timeout',
        doc='consider scan as failed if scan time exceeds timeout (in seconds)',
        default=3600,
        type=int,
    ),
)


FILTER_ATTRIBUTES = (
    AttributeSpec.required(
        name='type',
        doc='defines type to apply filter on, (component|source|resource)',
        type=str,
    ),
    AttributeSpec.required(
        name='match',
        doc='matches artifacts (list of regex|true|false)',
        type=typing.Union[dict, bool],
    ),
    AttributeSpec.required(
        name='action',
        doc='defines action to matched artifacts (include|exclude)',
        type=str,
    ),
)


class FilterCfg(ModelBase):
    @classmethod
    def _attribute_specs(cls):
        return FILTER_ATTRIBUTES

    def type(self):
        return self.raw['type']

    def match(self):
        return self.raw['match']

    def action(self):
        return self.raw['action']


class CheckmarxCfg(ModelBase):
    @classmethod
    def _attribute_specs(cls):
        return CHECKMARX_ATTRIBUTES

    def team_id(self):
        return self.raw['team_id']

    def severity_threshold(self) -> int:
        return self.raw.get('severity_threshold')

    def checkmarx_cfg_name(self):
        return self.raw.get('cfg_name')

    def include_path_regexes(self):
        return self.raw['include_path_regexes']

    def exclude_path_regexes(self):
        return self.raw['exclude_path_regexes']

    def scan_timeout(self) -> int:
        return self.raw.get('scan_timeout')


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
    AttributeSpec.optional(
        name='filters',
        default={},
        doc='config to include and exclude sources, resources or whole components',
        type=FilterCfg,
    ),
    AttributeSpec.optional(
        name='checkmarx',
        type=CheckmarxCfg,
        default=(),
        doc='if present, perform checkmarx scanning',
    ),
    AttributeSpec.optional(
        name='issue_policies',
        default=IssuePolicies(),
        type=IssuePolicies,
        doc='defines issues policies (e.g. SLAs for maximum processing times',
    ),
    AttributeSpec.optional(
        name='overwrite_github_issues_tgt_repository_url',
        default=None,
        doc='if set, and notify is set to github_issues, overwrite target github repository',
    ),
    AttributeSpec.optional(
        name='github_issue_templates',
        default=None,
        doc='''\
        use to configure custom github-issue-templates (sub-attr: `body`)
        use python3's format-str syntax
        available variables:
        - summary # contains name, version, etc in a table
        - component_name
        - component_version
        - resource_name
        - resource_version
        - resource_type
        - greatest_cve
        - report_url
        - delivery_dashboard_url
        ''',
        type=list[GithubIssueTemplateCfg],
    ),
    AttributeSpec.optional(
        name='github_issue_labels_to_preserve',
        default=None,
        doc='optional list of regexes for labels that will never be removed upon ticket-update',
        type=list[str],
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

    def filters(self):
        if filters := self.raw.get('filters'):
            return FilterCfg(filters)

    def filters_raw(self):
        if filters := self.raw.get('filters'):
            return filters

    def transformer(self):
        return SourceScanTraitTransformer(trait=self)

    def custom_init(self, raw_dict: dict):
        if self.checkmarx():
            return True
        else:
            # TODO should actually raise something, but breaks docu generation
            ci.util.warning('checkmarx should be defined.')

    def issue_policies(self) -> IssuePolicies:
        if isinstance((v := self.raw['issue_policies']), IssuePolicies):
            return v

        return dacite.from_dict(
            data_class=IssuePolicies,
            data=v,
        )

    def overwrite_github_issues_tgt_repository_url(self) -> typing.Optional[str]:
        return self.raw.get('overwrite_github_issues_tgt_repository_url')

    def github_issue_templates(self) -> list[GithubIssueTemplateCfg]:
        if not (raw := self.raw.get('github_issue_templates')):
            return None

        template_cfgs = [
            dacite.from_dict(
                data_class=GithubIssueTemplateCfg,
                data=cfg,
            ) for cfg in raw
        ]

        return template_cfgs

    def github_issue_template(self, type: str) -> typing.Optional[GithubIssueTemplateCfg]:
        if not (template_cfgs := self.github_issue_templates()):
            return None

        for cfg in template_cfgs:
            if cfg.type == type:
                return cfg

        return None

    def github_issue_labels_to_preserve(self) -> typing.Optional[list[str]]:
        return self.raw['github_issue_labels_to_preserve']


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
            pull_request_notification_policy=PullRequestNotificationPolicy.NO_NOTIFICATION,
            injecting_trait_name=self.name,
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
        component_descriptor_step = pipeline_args.step(
            concourse.model.traits.component_descriptor.DEFAULT_COMPONENT_DESCRIPTOR_STEP_NAME
        )
        self.source_scan_step._add_dependency(component_descriptor_step)

    @classmethod
    def dependencies(cls):
        return {'component_descriptor'}
