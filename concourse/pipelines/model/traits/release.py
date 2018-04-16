from util import ensure_not_none

from concourse.pipelines.modelbase import (
  PipelineStep,
  Trait,
  TraitTransformer
)


class ReleaseTrait(Trait):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def nextversion(self):
        return self.raw.get('nextversion', 'bump_minor')

    def transformer(self):
        return ReleaseTraitTransformer(name=self.name)


class ReleaseTraitTransformer(TraitTransformer):
    def inject_steps(self):
        # inject 'release' step
        self.release_step = PipelineStep(name='release', raw_dict={}, is_synthetic=True)
        yield self.release_step

    def process_pipeline_args(self, pipeline_args: 'PipelineArgs'):
        # we depend on all other steps
        for step in pipeline_args.steps():
            self.release_step._add_dependency(step)

        # a 'release job' should never be triggered automatically
        main_repo = pipeline_args.main_repository()
        if main_repo:
            main_repo._trigger = False

    def depends(self):
        return {'publish'}

