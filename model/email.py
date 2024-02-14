# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


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


def smtp_password_from_aws_access_key(
    aws_secret_access_key: str,
    aws_region: str,
):
    '''
    computes a password that be used for smtp-basic-auth for amazon's SES (simple email service)
    from given aws-secret-key and region

    stolen from https://docs.aws.amazon.com/ses/latest/dg/smtp-credentials.html
    '''
    # These values are required to calculate the credentials for AWS SES. Do not change them.
    date = "11111111"
    version = b'\x04'

    def sign(key, msg):
        return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()

    # do not change order or hard-coded values (see linked documentation above)
    signature = sign(f'AWS4{aws_secret_access_key}'.encode('utf-8'), date)
    signature = sign(signature, aws_region)
    signature = sign(signature, 'ses') # service
    signature = sign(signature, 'aws4_request') # terminal
    signature = sign(signature, 'SendRawEmail') # message
    signature_and_version = version + signature
    smtp_password = base64.b64encode(signature_and_version)

    return smtp_password.decode('utf-8')


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
        if not cfg_factory:
            cfg_factory = ctx.cfg_factory()

        aws_config = cfg_factory.aws(self.aws_config_name())

        aws_secret_access_key = aws_config.secret_access_key()
        aws_region = aws_config.region()

        return smtp_password_from_aws_access_key(
            aws_secret_access_key=aws_secret_access_key,
            aws_region=aws_region,
        )

    def _required_attributes(self):
        return ['aws_config_name']


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
