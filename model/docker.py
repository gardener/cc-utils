# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


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
