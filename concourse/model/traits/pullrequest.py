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

import model.base

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
    Trait,
    TraitTransformer,
    ModelBase,
    ScriptType,
)

POLICIES_ATTRIBS = (
    AttributeSpec.optional(
        name='require-label',
        default='reviewed/ok-to-test',
        doc='the label required for PR build to start',
    ),
    AttributeSpec.optional(
        name='replacement-label',
        default='needs/ok-to-test',
        doc='the label set after require-label was removed by PR build',
    ),
)


class PullRequestPolicies(ModelBase):
    @classmethod
    def _attribute_specs(cls):
        return POLICIES_ATTRIBS

    def require_label(self):
        return self.raw.get('require-label')

    def replacement_label(self):
        return self.raw.get('replacement-label')


ATTRIBUTES = (
    AttributeSpec.optional(
        name='policies',
        default={
            'require-label': 'reviewed/ok-to-test',
            'replacement-label': 'needs/ok-to-test',
        },
        doc='configures the policies to apply to pull-requests',
        type=PullRequestPolicies,
    ),
    AttributeSpec.optional(
        name='disable-status-report',
        default=[],
        doc='a list of names of steps which shall not report their status to the pull request.',
        type=list,
    ),
)


class PullRequestTrait(Trait):
    @classmethod
    def _attribute_specs(self):
        return ATTRIBUTES

    def policies(self):
        policies_dict = self.raw['policies']
        return PullRequestPolicies(raw_dict=policies_dict)

    def disable_status_report(self):
        return self.raw.get('disable-status-report')

    def transformer(self):
        return PullRequestTraitTransformer(trait=self)


class PullRequestTraitTransformer(TraitTransformer):
    name = 'pull-request'

    def __init__(self, trait, *args, **kwargs):
        self.trait = trait
        super().__init__(*args, **kwargs)

    def inject_steps(self):
        # declare no dependencies --> run asap, but do not block other steps
        rm_pr_label_step = PipelineStep(
                name='rm_pr_label',
                raw_dict={},
                is_synthetic=True,
                notification_policy=StepNotificationPolicy.NO_NOTIFICATION,
                pull_request_notification_policy=PullRequestNotificationPolicy.NO_NOTIFICATION,
                injecting_trait_name=self.name,
                script_type=ScriptType.PYTHON3
        )
        rm_pr_label_step.set_timeout(duration_string='5m')
        yield rm_pr_label_step

    def process_pipeline_args(self, pipeline_args: JobVariant):
        repo_name = pipeline_args.main_repository().logical_name()

        # convert main-repo to PR
        pr_repo = pipeline_args.pr_repository(repo_name)
        pr_repo._trigger = True

        # patch-in the updated repository
        pipeline_args._repos_dict[repo_name] = pr_repo

        # patch the configured steps so that they do not report their status back to PRs
        for step_name in self.trait.disable_status_report():
            if not pipeline_args.has_step(step_name):
                raise model.base.ModelValidationError(
                    f"Reporting to pull requests was disabled for step '{step_name}', but no step "
                    f"'{step_name}' was found in job '{pipeline_args.variant_name}'"
                )
            step = pipeline_args.step(step_name)
            step._pull_request_notification_policy = PullRequestNotificationPolicy.NO_NOTIFICATION
