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
