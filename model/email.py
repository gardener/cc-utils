# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
import typing

import reutil
from model.base import (
    BasicCredentials,
    NamedModelElement,
)


class EmailConfig(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''

    def smtp_host(self):
        return self.raw.get('host')

    def smtp_port(self):
        return self.raw.get('port')

    def use_tls(self):
        return self.raw.get('use_tls')

    def sender_name(self):
        return self.raw.get('sender_name')

    def credentials(self):
        return EmailCredentials(self.raw.get('credentials'))

    def has_credentials(self):
        if self.raw.get('credentials'):
            return True
        return False

    def filter_recipients(self, recipients:typing.Iterable[str]):
        blacklist = self.raw.get('blacklist')
        if blacklist:
            email_filter = reutil.re_filter(exclude_regexes=blacklist)
            return {r for r in recipients if email_filter(r)}
        return recipients

    def _required_attributes(self):
        return ['host', 'port', 'credentials']

    def validate(self):
        super().validate()
        # ensure credentials are valid - validation implicitly happens in the constructor.
        if self.has_credentials():
            self.credentials()


class EmailCredentials(BasicCredentials):
    '''
    Not intended to be instantiated by users of this module
    '''
    pass
