from util import ensure_not_none

from concourse.pipelines.modelbase import (
  Trait,
  TraitTransformer,
)


class SchedulingTrait(Trait):
    # XXX: merge this with cron-trait
    def transformer(self):
        return SchedulingTraitTransformer(name=self.name)

    def suppress_parallel_execution(self):
        return self.raw.get('suppress_parallel_execution', False)


class SchedulingTraitTransformer(TraitTransformer):
    def process_pipeline_args(self, pipeline_args: 'PipelineArgs'):
        # no-op
        pass
