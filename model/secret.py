# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
# SPDX-License-Identifier: Apache-2.0

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

    def generation(self) -> int:
        if not self.raw.get('generation'):
            return None
        return int(self.raw.get('generation'))

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
