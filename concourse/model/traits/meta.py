# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


from concourse.model.job import (
    JobVariant,
)
from concourse.model.step import (
    PipelineStep,
    PullRequestNotificationPolicy,
    StepNotificationPolicy,
)
from concourse.model.base import (
  TraitTransformer,
  ScriptType,
)

ENV_VAR_NAME = 'meta'
DIR_NAME = 'meta'
META_STEP_NAME = 'meta'


class MetaTraitTransformer(TraitTransformer):
    name = 'meta'

    def inject_steps(self):
        self.meta_step = PipelineStep(
            name='meta',
            raw_dict={},
            is_synthetic=True,
            notification_policy=StepNotificationPolicy.NO_NOTIFICATION,
            pull_request_notification_policy=PullRequestNotificationPolicy.NO_NOTIFICATION,
            injecting_trait_name=self.name,
            script_type=ScriptType.PYTHON3,
        )
        self.meta_step.add_output(name=DIR_NAME, variable_name=ENV_VAR_NAME)
        yield self.meta_step

    def process_pipeline_args(self, pipeline_args: JobVariant):
        # All steps depend on meta step and receive an input from it
        for step in pipeline_args.steps():
            if step == self.meta_step:
                continue
            step._add_dependency(self.meta_step)
            step.add_input(name=DIR_NAME, variable_name=ENV_VAR_NAME)
        if pipeline_args.has_trait('version'):
            # All steps depend on version. Remove ourself to avoid circular dependency
            version_step = pipeline_args.step('version')
            self.meta_step._remove_dependency(version_step)
            self.meta_step.remove_input('version_path')
