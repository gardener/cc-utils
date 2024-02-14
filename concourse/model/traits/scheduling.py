# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


from concourse.model.job import (
    JobVariant,
)
from concourse.model.base import (
  AttributeSpec,
  Trait,
  TraitTransformer,
)


ATTRIBUTES = (
    AttributeSpec.optional(
        name='suppress_parallel_execution',
        default=None,
        doc='whether parallel executions of the same job should be prevented',
        type=bool,
    ),
)


class SchedulingTrait(Trait):
    @classmethod
    def _attribute_specs(cls):
        return ATTRIBUTES

    # XXX: merge this with cron-trait
    def transformer(self):
        return SchedulingTraitTransformer()

    def suppress_parallel_execution(self):
        return self.raw.get('suppress_parallel_execution', None)


class SchedulingTraitTransformer(TraitTransformer):
    name = 'scheduling'

    def process_pipeline_args(self, pipeline_args: JobVariant):
        # no-op
        pass
