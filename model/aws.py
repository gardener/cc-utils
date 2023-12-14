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

import dacite

import model.base


class AwsProfile(model.base.NamedModelElement):
    def region(self):
        return self.raw['region']

    def access_key_id(self):
        return self.raw['access_key_id']

    def secret_access_key(self):
        return self.raw['secret_access_key']

    def rotation_cfg(self) -> model.base.CfgElementReference:
        '''
        used to specify cfg-element to use for cross-rotation
        '''
        raw = self.raw.get('rotation_cfg')
        if raw:
            return dacite.from_dict(
                data_class=model.base.CfgElementReference,
                data=raw,
            )

        return None

    def iam_user_name(self):
        '''
        used to specify own iam user when rotating with cross-rotation
        '''
        return self.raw.get('iam_user_name')

    def _required_attributes(self):
        static_attributes = [
            'region',
            'access_key_id',
            'secret_access_key',
        ]
        dynamic_attributes = ['iam_user_name'] if self.rotation_cfg() else []

        return static_attributes + dynamic_attributes
