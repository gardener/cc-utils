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
