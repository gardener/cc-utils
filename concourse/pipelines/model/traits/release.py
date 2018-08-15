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

from concourse.pipelines.model.step import PipelineStep
from concourse.pipelines.modelbase import (
  Trait,
  TraitTransformer
)


class ReleaseTrait(Trait):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _defaults_dict(self):
        return {
            'nextversion': 'bump_minor',
        }

    def nextversion(self):
        return self.raw['nextversion']

    def transformer(self):
        return ReleaseTraitTransformer(name=self.name)


class ReleaseTraitTransformer(TraitTransformer):
    def inject_steps(self):
        # inject 'release' step
        self.release_step = PipelineStep(name='release', raw_dict={}, is_synthetic=True)
        yield self.release_step

    def process_pipeline_args(self, pipeline_args: 'JobVariant'):
        # we depend on all other steps
        for step in pipeline_args.steps():
            self.release_step._add_dependency(step)

        # a 'release job' should only be triggered automatically if explicitly configured
        main_repo = pipeline_args.main_repository()
        if main_repo:
            if not 'trigger' in pipeline_args.raw['repo']:
                main_repo._trigger = False

    def dependencies(self):
        return super().dependencies() | {'version'}

    def order_dependencies(self):
        return super().dependencies() | {'publish'}

