# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import model.base


class SigningServerConfig(model.base.NamedModelElement):
    def private_key(self) -> str:
        return self.raw['private_key']

    def certificate(self):
        return self.raw['certificate']

    def ca_certificates(self):
        return self.raw['ca_certificates']
