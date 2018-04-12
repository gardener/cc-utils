# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

import smtplib

from util import ensure_file_exists, ensure_not_empty, fail, CliHint
from mail import template_mailer as mailer

def send_mail(
    smtp_server: str,
    smtp_user: str,
    smtp_passwd: str,
    sender_name: CliHint(help="To' header for the sent email(s), can be an arbitrary string."),
    sender_email: CliHint(help="Sender email address that is specified for sendmail against the smtp server."),
    recipients: CliHint(typehint=[str], help="Recipient email address. Can be supplied multiple times."),
    mail_template: CliHint(help="Template file used as input to define the mail body. Replace-tokens possible."),
    subject: CliHint(help="Subject of mail send."),
    cc_recipients: CliHint(typehint=[str], help="Carbon copy email address. Can be supplied multiple times.")=[],
    replace_token: CliHint(typehint=[str], help="<key>=<value> will replace all occurrences in the mail body.")=[],
    replace_token_file: CliHint(typehint=[str], help="<key>=<value> pairs file. Can be supplied multiple times.")=[],
    use_tls: CliHint(help="SMTP TLS enabled.")=True
):
    '''
    @param sender_name: The specified value is used as the 'To' header for the sent email(s) and
        can be an arbitrary string.
    @param sender_email: This parameter is the email address that is specified as sender email
        address when issuing the sendmail command against the smtp server.
    @param recipients: This parameter is the email address that is specified as recipient email
        address when issuing the sendmail command against the smtp server. Can be supplied multiple
        times.
    @param mail_template: The template file is used as an input to define the mail body. Can be any
        text file. It can in addition contain "replace-tokens", e.g. "REPLACE_ME". If such
        replace-tokens are defined, they can be replaced using the --replace-token or
        --replace-token-file arguments, which is intended to generate similar mails based on a
        template.
    @param subject: Subject of mail send.
    @param cc_recipients: This parameter is the email address that is specified as carbon copy
        recipient email address when issuing the sendmail command against the smtp server. Can be
        supplied multiple times.
    @param replace_token: This parameter is used in combination with the --mail-template argument
        and "replace-tokens" defined in the mail body. Specified replace-tokens will replace all
        occurrences of matching "replace-tokens" in the mail body, e.g. "REPLACE_ME". All
        replace-tokens must be of form <key>=<value>. Can be supplied multiple times.
    @param replace_token_file: Like the replace-token mechanism explained for the --replace-token
        argument, this parameter allows specifying a file for replace tokens - an alternate way to
        provide replace tokens. All replace-tokens must be of form <key>=<value>. Tokens are
        separated by line-breaks. Can be supplied multiple times.
    @param use_tls: SMTP TLS enabled.
    '''
    ensure_not_empty(sender_name)
    ensure_not_empty(sender_email)
    ensure_not_empty(recipients)
    ensure_file_exists(mail_template)
    ensure_not_empty(subject)
    for rtf in replace_token_file:
        ensure_file_exists(rtf)

    # validate template-tokens
    invalid_tokens = filter(lambda t: not isinstance(t, str) or not '=' in t, replace_token)
    if len(list(invalid_tokens)) > 0:
        fail('all replace-tokens must be of form <key>=<value>: ' + ' '.join(
            invalid_tokens
            )
        )

    # parse replace-tokens
    tokens = map(lambda t: t.split('=', 1), replace_token)

    # create body from template
    mail_body = mailer.create_body(
        template_file=mail_template,
        replace_tokens=tokens,
        replace_token_files=replace_token_file
    )

    # create mail envelope
    mail = mailer.create_mail(
        subject=subject,
        sender=sender_name,
        recipients=recipients,
        cc_recipients=cc_recipients,
        text=mail_body
    )

    if use_tls:
        smtp_server = smtplib.SMTP_SSL(smtp_server)
    else:
        smtp_server = smtplib.SMTP(smtp_server)
    if smtp_user and smtp_passwd:
        smtp_server.login(user=smtp_user, password=smtp_passwd)

    mailer.send_mail(
        smtp_server=smtp_server,
        msg=mail,
        sender=sender_email,
        recipients=recipients + cc_recipients
    )
