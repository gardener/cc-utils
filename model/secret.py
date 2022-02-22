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


import enum
import dataclasses

from model.base import (
    ModelValidationError,
    NamedModelElement,
)


class Cipher(enum.Enum):
    AES_ECB = 'AES.ECB'
    PLAINTEXT = 'PLAINTEXT'


class Secret(NamedModelElement):
    def key(self) -> bytes:
        if self.generation():
            return self.key_from_gen()
        else:
            key = self.raw.get('key')
            return key.encode('utf-8') if key else None

    def key_from_gen(self) -> bytes:
        key = self.raw.get(f'key-{self.generation()}')
        return key.encode('utf-8') if key else None

    def cipher_algorithm(self):
        return Cipher(self.raw.get('cipher_algorithm'))

    def generation(self):
        return self.raw.get('generation')

    def _required_attributes(self):
        return ['cipher_algorithm',]

    def _validate_required_attributes(self):
        super()._validate_required_attributes()
        key_attrs = [k for k in self.raw.keys() if k.startswith('key-')]
        if not 'key' in self.raw and not key_attrs:
            raise  ModelValidationError("Either 'key' or 'key-<number>' key must be present")


@dataclasses.dataclass
class SecretData:
    key: bytes
    cipher_algorithm: str
