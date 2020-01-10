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

from concourse.model.pipeline import PipelineDefinition
from concourse.model.job import JobVariant


class PipelineDefinitionValidator(object):
    def __init__(self, pipeline_definition):
        if not isinstance(pipeline_definition, PipelineDefinition):
            raise ValueError('not a PipelineDefinition: ' + str(pipeline_definition))

        self._pipeline_definition = pipeline_definition

    def validate(self):
        for variant in self._pipeline_definition.variants():
            self._validate_variant(variant)

    def _validate_variant(self, variant: JobVariant):
        if not isinstance(variant, JobVariant):
            raise ValueError('not a JobVariant: ' + str(variant))
        self._validate_element(variant)

    def _validate_element(self, element, parents=set()):
        for child in element._children():
            self._validate_element(child, parents | {element})
        element.validate()
