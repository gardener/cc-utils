# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

from model.base import (
    NamedModelElement,
)


class AwsProfile(NamedModelElement):
    def region(self):
        return self.raw['region']

    def access_key_id(self):
        return self.raw['access_key_id']

    def secret_access_key(self):
        return self.raw['secret_access_key']

    def _required_attributes(self):
        return ['region','access_key_id','secret_access_key']
