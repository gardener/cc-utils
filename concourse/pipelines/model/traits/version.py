from util import ensure_not_none

from concourse.pipelines.modelbase import (
  PipelineStep,
  Trait,
  TraitTransformer
)


class VersionTrait(Trait):
    PREPROCESS_OPS = {'finalise', 'inject-commit-hash'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if not self._preprocess() in self.PREPROCESS_OPS:
            raise ValueError('preprocess must be one of: ' + ', '.join(self.PREPROCESS_OPS))

    def _preprocess(self):
        return self.raw.get('preprocess', 'inject-commit-hash')
        self.args = ensure_not_none(trait_args)

    def versionfile_relpath(self):
        return self.raw.get('versionfile', 'VERSION')

    def inject_effective_version(self):
        return self.raw.get('inject_effective_version', False)

    def transformer(self):
        return VersionTraitTransformer(name=self.name)



class VersionTraitTransformer(TraitTransformer):
    def inject_steps(self):
        self.version_step = PipelineStep(name='version', raw_dict={}, is_synthetic=True)
        self.version_step.add_output(name='version_path', variable_name='managed-version')

        yield self.version_step

    def process_pipeline_args(self, pipeline_args: 'PipelineArgs'):
        # all steps depend from us and may consume our output
        for step in pipeline_args.steps():
            if step == self.version_step:
                continue
            step._add_dependency(self.version_step)
            step.add_input(name='version_path', variable_name='managed-version')


