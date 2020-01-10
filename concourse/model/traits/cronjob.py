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
