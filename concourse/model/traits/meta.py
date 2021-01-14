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

from concourse.model.job import (
    JobVariant,
)
from concourse.model.step import (
    PipelineStep,
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
            injected_by_trait=self.name,
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
