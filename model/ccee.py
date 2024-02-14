# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


from model.base import (
    BasicCredentials,
    NamedModelElement,
    ModelBase,
)


class CCEEProject(ModelBase):
    def region(self):
        return self.raw.get('region')

    def name(self):
        return self.raw.get('name')

    def domain(self):
        return self.raw.get('domain')

    def auth_url(self):
        return self.raw.get('auth_url')


class CCEEConfig(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''

    def _required_attributes(self):
        return ['credentials']

    def credentials(self):
        return BasicCredentials(self.raw['credentials'])

    def projects(self):
        return [
            CCEEProject(raw_dict=project_dict) for project_dict in self.raw['projects']
        ]

    def _defaults_dict(self):
        return {
            'projects': (),
        }

    def _optional_attributes(self):
        return (
            'projects',
        )
