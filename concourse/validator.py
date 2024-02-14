# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


from concourse.model.pipeline import PipelineDefinition
from concourse.model.job import JobVariant


class PipelineDefinitionValidator:
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
