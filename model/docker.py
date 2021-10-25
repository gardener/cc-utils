# Copyright (c) 2019-2021 SAP SE or an SAP affiliate company. All rights reserved. This file is
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

from model.base import NamedModelElement


class DockerConfig(NamedModelElement):
    def username(self):
        return self.raw.get('username')

    def email_address(self):
        return self.raw.get('email_address')

    def password(self):
        return self.raw.get('password')

    def access_tokens(self):
        return self.raw.get('access_tokens')

    def _required_attributes(self):
        return (
            'username',
            'email_address',
            'password',
        )

    def _optional_attributes(self):
        return ('access_tokens',)
