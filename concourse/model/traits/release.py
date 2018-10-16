# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
# under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

from concourse.model.step import PipelineStep
from concourse.model.base import (
  AttributeSpec,
  Trait,
  TraitTransformer
)


ATTRIBUTES = (
    AttributeSpec.optional(
        name='nextversion',
        default='bump_minor',
        doc='specifies how the next development version is to be calculated',
    ),
    AttributeSpec.optional(
        name='release_callback',
        default=None,
        doc='a callback to invoke when creating a release commit',
    ),
    AttributeSpec.optional(
        name='rebase_before_release',
        default=False,
        doc='''
        whether or not a rebase against latest branch head should be done before publishing
        release commits.
        ''' ,
    ),
)


class ReleaseTrait(Trait):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _attribute_specs(self):
        return ATTRIBUTES

    def _defaults_dict(self):
        return AttributeSpec.defaults_dict(ATTRIBUTES)

    def _optional_attributes(self):
        return set(AttributeSpec.optional_attr_names(ATTRIBUTES))

    def nextversion(self):
        return self.raw['nextversion']

    def release_callback_path(self):
        return self.raw['release_callback']

    def rebase_before_release(self):
        return self.raw['rebase_before_release']

    def transformer(self):
        return ReleaseTraitTransformer()


class ReleaseTraitTransformer(TraitTransformer):
    name = 'release'

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
            if 'trigger' not in pipeline_args.raw['repo']:
                main_repo._trigger = False

    @classmethod
    def dependencies(cls):
        return {'version'}

    @classmethod
    def order_dependencies(cls):
        return {'publish'}
