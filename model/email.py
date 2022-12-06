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
import base64
import hashlib
import hmac
import typing

import reutil

import ctx
from model.base import (
    BasicCredentials,
    NamedModelElement,
)

# These values are required to calculate the credentials for AWS SES. Do not change them.
DATE = "11111111"
SERVICE = "ses"
MESSAGE = "SendRawEmail"
TERMINAL = "aws4_request"
VERSION = 0x04


class EmailCredentials(BasicCredentials):
    '''
    Not intended to be instantiated by users of this module
    '''
    pass


class AwsSesCredentials(EmailCredentials):
    def aws_config_name(self) -> str:
        return self.raw['aws_config_name']

    def username(self, cfg_factory=None) -> str:
        if not cfg_factory:
            cfg_factory = ctx.cfg_factory()
        return cfg_factory.aws(self.aws_config_name()).access_key_id()

    def passwd(self, cfg_factory=None) -> str:
        return self._calculate_ses_key(cfg_factory=cfg_factory)

    def _required_attributes(self):
        return ['aws_config_name']

    def _calculate_ses_key(self, cfg_factory=None) -> str:
        if not cfg_factory:
            cfg_factory = ctx.cfg_factory()

        aws_config = cfg_factory.aws(self.aws_config_name())

        # taken and adjusted from from
        # https://docs.aws.amazon.com/ses/latest/dg/smtp-credentials.html

        def sign(key, msg):
            return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()

        signature = sign(("AWS4" + aws_config.secret_access_key()).encode('utf-8'), DATE)
        signature = sign(signature, aws_config.region())
        signature = sign(signature, SERVICE)
        signature = sign(signature, TERMINAL)
        signature = sign(signature, MESSAGE)
        signature_and_version = bytes([VERSION]) + signature
        smtp_password = base64.b64encode(signature_and_version)

        return smtp_password.decode('utf-8')


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

    def credentials(self) -> EmailCredentials:
        if credentials := self.raw.get('credentials'):
            if 'aws_config_name' in credentials:
                return AwsSesCredentials(credentials)

        return EmailCredentials(credentials)

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
