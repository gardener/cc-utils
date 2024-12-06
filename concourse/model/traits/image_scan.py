# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import dataclasses
import enum
import typing

import dacite

import github.issue

from concourse.model.job import (
    JobVariant,
)
from concourse.model.step import (
    PipelineStep,
    PullRequestNotificationPolicy,
)
from concourse.model.base import (
    AttributeSpec,
    Trait,
    TraitTransformer,
    ModelBase,
    ScriptType,
)
import github.compliance.model as gcm
from model.base import ModelValidationError

import concourse.model.traits.component_descriptor


OS_ID_SCAN_ATTRS = (
    AttributeSpec.optional(
        name='parallel_jobs',
        default=8,
        doc='amount of parallel jobs to run',
        type=int,
    ),
    AttributeSpec.optional(
        name='timeout',
        default='2h',
        doc='''
        go-style time interval (e.g.: '1h30m') after which the image-scan-step will be interrupted
        and fail.
        ''',
        type=str,
    ),
)


class OsIdScan(ModelBase):
    @classmethod
    def _attribute_specs(cls):
        return OS_ID_SCAN_ATTRS

    def parallel_jobs(self) -> int:
        return int(self.raw['parallel_jobs'])

    def timeout(self):
        return self.raw.get('timeout')


class Notify(enum.Enum):
    EMAIL_RECIPIENTS = 'email_recipients'
    NOBODY = 'nobody'
    COMPONENT_OWNERS = 'component_owners'
    GITHUB_ISSUES = 'github_issues'


@dataclasses.dataclass(frozen=True)
class IssuePolicies:
    max_processing_time_days: gcm.MaxProcessingTimesDays = gcm.MaxProcessingTimesDays()


ATTRIBUTES = (
    AttributeSpec.optional(
        name='notify',
        default=Notify.EMAIL_RECIPIENTS,
        doc='whom to notify about found issues',
        type=Notify,
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

        .. code-block::
          :caption: available variables

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
        type=list[github.issue.GithubIssueTemplateCfg],
    ),
    AttributeSpec.optional(
        name='github_issue_labels_to_preserve',
        default=None,
        doc='optional list of regexes for labels that will never be removed upon ticket-update',
        type=list[str],
    ),
    AttributeSpec.optional(
        name='email_recipients',
        default=[],
        doc='optional list of email recipients to be notified about critical scan results',
        type=typing.List[str],
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
    ),
)


class ImageScanTrait(Trait):
    @classmethod
    def _attribute_specs(cls):
        return ATTRIBUTES

    def issue_policies(self) -> IssuePolicies:
        if isinstance((v := self.raw['issue_policies']), IssuePolicies):
            return v

        return dacite.from_dict(
            data_class=IssuePolicies,
            data=v,
        )

    def notify(self) -> Notify:
        return Notify(self.raw['notify'])

    def overwrite_github_issues_tgt_repository_url(self) -> typing.Optional[str]:
        return self.raw.get('overwrite_github_issues_tgt_repository_url')

    def github_issue_templates(self) -> list[github.issue.GithubIssueTemplateCfg]:
        if not (raw := self.raw.get('github_issue_templates')):
            return None

        template_cfgs = [
            dacite.from_dict(
                data_class=github.issue.GithubIssueTemplateCfg,
                data=cfg,
            ) for cfg in raw
        ]

        return template_cfgs

    def github_issue_template(self, type: str) -> github.issue.GithubIssueTemplateCfg | None:
        if not (template_cfgs := self.github_issue_templates()):
            return None

        for cfg in template_cfgs:
            if cfg.type == type:
                return cfg

        return None

    def github_issue_labels_to_preserve(self) -> typing.Optional[list[str]]:
        return self.raw['github_issue_labels_to_preserve']

    def email_recipients(self):
        return self.raw['email_recipients']

    def os_id(self):
        if (raw := self.raw.get('os_id')) is not None:
            return OsIdScan(raw_dict=raw)

    def transformer(self):
        return ImageScanTraitTransformer(trait=self)

    def trait_depends(self):
        return set(self.raw['trait_depends'])

    def validate(self):
        super().validate()


class ImageScanTraitTransformer(TraitTransformer):
    name = 'image_scan'

    def __init__(self, trait, *args, **kwargs):
        self.trait = trait
        super().__init__(*args, **kwargs)

    def inject_steps(self):
        if self.trait.os_id():
            self.os_id_step = PipelineStep(
                    name='os-id-scan',
                    raw_dict={},
                    is_synthetic=True,
                    pull_request_notification_policy=PullRequestNotificationPolicy.NO_NOTIFICATION,
                    injecting_trait_name=self.name,
                    script_type=ScriptType.PYTHON3
            )
            self.os_id_step.add_input(
                name=concourse.model.traits.component_descriptor.DIR_NAME,
                variable_name=concourse.model.traits.component_descriptor.ENV_VAR_NAME,
            )
            self.os_id_step.set_timeout(duration_string=self.trait.os_id().timeout())
            yield self.os_id_step

    def process_pipeline_args(self, pipeline_args: JobVariant):
        # our steps depends on dependency descriptor step
        component_descriptor_step = pipeline_args.step(
            concourse.model.traits.component_descriptor.DEFAULT_COMPONENT_DESCRIPTOR_STEP_NAME
        )
        if self.trait.os_id():
            self.os_id_step._add_dependency(component_descriptor_step)

        for trait_name in self.trait.trait_depends():
            if not pipeline_args.has_trait(trait_name):
                raise ModelValidationError(f'dependency towards absent trait: {trait_name}')

    @classmethod
    def dependencies(cls):
        return {'component_descriptor'}

    @classmethod
    def order_dependencies(cls):
        # required in case image-scanning should be done after publish
        # (-> auto-injected dependency for "prepare"-step towards _all_ steps)
        return {'publish'}
