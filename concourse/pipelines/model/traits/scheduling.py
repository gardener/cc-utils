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
        return self.raw.get('suppress_parallel_execution', None)


class SchedulingTraitTransformer(TraitTransformer):
    def process_pipeline_args(self, pipeline_args: 'PipelineArgs'):
        # no-op
        pass
