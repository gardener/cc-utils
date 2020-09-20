# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

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
            script_type=ScriptType.PYTHON3,
        )
        self.release_step.set_timeout(duration_string='10m')
        yield self.release_step

    def process_pipeline_args(self, pipeline_args: JobVariant):
        pass

    @classmethod
    def dependencies(cls):
        return {'version'}
