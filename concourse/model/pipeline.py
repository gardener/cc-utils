# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

class PipelineDefinition(object):
    def __init__(self):
        self._variants_dict = {}
        self._resource_registry = None

    def resource_registry(self):
        return self._resource_registry

    def variants(self):
        return self._variants_dict.values()

    def variant(self, name: str):
        return self._variants_dict[name]
