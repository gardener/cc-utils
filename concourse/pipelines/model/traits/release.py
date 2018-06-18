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

    def dependencies(self):
        return super().dependencies() | {'publish'}

