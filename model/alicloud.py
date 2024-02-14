# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


from model.base import (
    NamedModelElement,
)


class AlicloudConfig(NamedModelElement):
    def region(self):
        return self.raw.get('region')

    def access_key_id(self):
        return self.raw.get('access_key_id')

    def access_key_secret(self):
        return self.raw.get('access_key_secret')

    def _required_attributes(self):
        return ['region', 'access_key_id', 'access_key_secret']
