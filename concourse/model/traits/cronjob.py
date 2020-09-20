# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

from concourse.model.job import JobVariant
from concourse.model.base import Trait, TraitTransformer, AttributeSpec

ATTRIBUTES = (
    AttributeSpec.optional(
        name='interval',
        default='5m',
        doc='''
        go-style time interval between job executions. Supported suffixes are: `s`, `m`, `h`
        ''',
    ),
)


class CronTrait(Trait):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @classmethod
    def _attribute_specs(cls):
        return ATTRIBUTES

    def interval(self):
        return self.raw['interval']

    def resource_name(self):
        # variant-names must be unique, so this should suffice
        return self.variant_name + '-' + self.interval() + '-cron'

    def transformer(self):
        return CronTraitTransformer()


class CronTraitTransformer(TraitTransformer):
    name = 'cronjob'

    def process_pipeline_args(self, pipeline_args: JobVariant):
        main_repo = pipeline_args.main_repository()
        if main_repo:
            if 'trigger' not in pipeline_args.raw['repo']:
                main_repo._trigger = False
        # todo: inject cron-resource
