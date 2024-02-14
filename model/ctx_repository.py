# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


from model.base import (
    NamedModelElement,
)


class CtxRepositoryCfg(NamedModelElement):
    '''
    a ctx-repository cfg, identifying a component-descriptor (v2) repository, defining a
    repository context.
    '''
    def base_url(self):
        return self.raw.get('base_url')

    def description(self):
        return self.raw.get('description', '<no description available>')

    def _required_attributes(self):
        return 'base_url',

    def _optional_attributes(self):
        return [
            'description',
        ]
