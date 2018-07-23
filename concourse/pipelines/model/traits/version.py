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


class VersionTrait(Trait):
    PREPROCESS_OPS = {'finalize', 'inject-commit-hash', 'noop', 'use-branch-name', 'inject-branch-name'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if not self._preprocess() in self.PREPROCESS_OPS:
            raise ValueError('preprocess must be one of: ' + ', '.join(self.PREPROCESS_OPS))

    def _preprocess(self):
        return self.raw.get('preprocess', 'inject-commit-hash')

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

    def process_pipeline_args(self, pipeline_args: 'JobVariant'):
        # all steps depend from us and may consume our output
        for step in pipeline_args.steps():
            if step == self.version_step:
                continue
            step._add_dependency(self.version_step)
            step.add_input(name='version_path', variable_name='managed-version')


