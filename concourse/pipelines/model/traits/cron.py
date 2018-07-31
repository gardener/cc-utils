# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

from util import not_none

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
    def process_pipeline_args(self, pipeline_args: 'JobVariant'):
        main_repo = pipeline_args.main_repository()
        if main_repo:
            if not 'trigger' in pipeline_args.raw['repo']:
                main_repo._trigger = False
        # todo: inject cron-resource


