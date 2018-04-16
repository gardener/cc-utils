from util import ensure_not_none

from concourse.pipelines.modelbase import Trait, TraitTransformer


class CronTrait(Trait):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def interval(self):
        return self.raw.get('interval', '5m')

    def resource_name(self):
        return self.variant_name + '-cron' # variant-names must be unique, so this should suffice

    def transformer(self):
        return CronTraitTransformer(name=self.name)


class CronTraitTransformer(TraitTransformer):
    def process_pipeline_args(self, pipeline_args: 'PipelineArgs'):
        # todo: inject cron-resource - until then, this is a noop
        pass

