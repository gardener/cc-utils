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
from concourse.model.job import (
  JobVariant,
)

ATTRIBUTES = (
    AttributeSpec.optional(
        name='preprocess',
        default='finalize',
        doc='version processing operation to set effective version',
    ),
)


class DraftReleaseTrait(Trait):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def transformer(self):
        return DraftReleaseTraitTransformer()

    @classmethod
    def _attribute_specs(cls):
        return ATTRIBUTES

    def _preprocess(self):
        return self.raw['preprocess']

    def validate(self):
        super().validate()
        if self._preprocess() != 'finalize':
            raise ModelValidationError(
                "Only 'finalize' is supported as value for 'preprocess' in draft_release trait"
            )


class DraftReleaseTraitTransformer(TraitTransformer):
    name = 'draft_release'

    def inject_steps(self):
        # inject 'release' step
        self.release_step = PipelineStep(
            name='create_draft_release_notes',
            raw_dict={},
            is_synthetic=True,
            notification_policy=StepNotificationPolicy.NO_NOTIFICATION,
            pull_request_notification_policy=PullRequestNotificationPolicy.NO_NOTIFICATION,
            injecting_trait_name=self.name,
            script_type=ScriptType.PYTHON3,
        )
        self.release_step.set_timeout(duration_string='10m')
        yield self.release_step

    def process_pipeline_args(self, pipeline_args: JobVariant):
        cd_trait = pipeline_args.trait('component_descriptor')
        cd_step = pipeline_args.step(cd_trait.step_name())
        self.release_step._add_dependency(cd_step)

    @classmethod
    def dependencies(cls):
        return {'version', 'component_descriptor'}
