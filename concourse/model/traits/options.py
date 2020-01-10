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

from ci.util import not_none
from concourse.model.base import (
    AttributeSpec,
    Trait,
    TraitTransformer
)
from concourse.model.job import (
    JobVariant,
)


ATTRIBUTES = (
    AttributeSpec.optional(
        name='build_logs_to_retain',
        default=1000,
        doc='the amount of build logs to retain before log rotation occurs',
        type=int,
    ),
    AttributeSpec.optional(
        name='public_build_logs',
        default=False,
        doc='whether or not build logs are accessible to unauthenticated users',
        type=bool,
    ),
)


class OptionsTrait(Trait):
    @classmethod
    def _attribute_specs(cls):
        return ATTRIBUTES

    def build_logs_to_retain(self):
        return self.raw['build_logs_to_retain']

    def public_build_logs(self):
        return self.raw['public_build_logs']

    def transformer(self):
        return OptionsTraitTransformer(trait=self)


class OptionsTraitTransformer(TraitTransformer):
    name = 'options'

    def __init__(self, trait: OptionsTrait, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.trait = not_none(trait)

    def process_pipeline_args(self, pipeline_args: JobVariant):
        pass
