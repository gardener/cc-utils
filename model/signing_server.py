# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import enum
import typing

import model.base


class SigningAlgorithm(enum.StrEnum):
    RSASSA_PSS = 'rsassa-pss'
    RSASSA_PKCS1_V1_5 = 'rsassa-pkcs1-v1_5'

    @staticmethod
    def as_rfc_standard(algorithm: typing.Union['SigningAlgorithm', str]) -> str:
        # parses the algorithm to the standard format described in
        # https://datatracker.ietf.org/doc/html/rfc3447
        algorithm = SigningAlgorithm(algorithm.lower())
        if algorithm is SigningAlgorithm.RSASSA_PSS:
            return 'RSASSA-PSS'
        elif algorithm is SigningAlgorithm.RSASSA_PKCS1_V1_5:
            return 'RSASSA-PKCS1-v1_5'
        else:
            raise NotImplementedError(algorithm)


class SigningServerEndpoint(model.base.NamedModelElement):
    def url(self) -> str:
        return self.raw.get('url')


class SigningServerConfig(model.base.NamedModelElement):
    def private_key(self) -> str:
        return self.raw['private_key']

    def certificate(self):
        return self.raw['certificate']

    def ca_certificates(self):
        return self.raw['ca_certificates']

    def algorithm(self) -> SigningAlgorithm:
        return SigningAlgorithm(self.raw.get('algorithm', SigningAlgorithm.RSASSA_PSS))

    def signature_name(self) -> str:
        return self.raw['signature_name']
