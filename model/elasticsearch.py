# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


from model.base import (
    BasicCredentials,
    NamedModelElement,
)


class ElasticSearchConfig(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''

    def endpoints(self):
        return self.raw['endpoints']

    def credentials(self):
        return ElasticSearchCredentials(raw_dict=self.raw['credentials'])

    def _required_attributes(self):
        return ('endpoint_url', 'endpoints')

    def _optional_attributes(self):
        return ('credentials',)


class ElasticSearchCredentials(BasicCredentials):
    pass
