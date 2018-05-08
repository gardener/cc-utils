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

import git
import smtplib
import typing

from model import EmailConfig
from util import (
    ensure_file_exists,
    ensure_directory_exists,
    ensure_not_empty,
    ensure_not_none,
    fail,
    CliHint,
    ctx,
    CliHints,
)
from mail import template_mailer as mailer

def send_mail(
    email_cfg_name: CliHint(help="reference to an email cfg (see repo cc-config / secrets-server)"),
    recipients: CliHint(typehint=[str], help="Recipient email address"),
    mail_template_file: CliHints.existing_file(),
    subject: CliHint(help="email subject"),
    cc_recipients: CliHint(typehint=[str], help="Carbon copy email address")=[],
    replace_token: CliHint(typehint=[str], help="<key>=<value> will replace all occurrences in the mail body.")=[],
):
    '''
    Sends an email using the specified email_cfg (retrieved from a cfg_factory) to the specified
    recipients. The mail body is read from a file. A simple token-replacement is done if
    (optional) replace-tokens are given.

    @param recipients: mail recipients (email addresses)
    @param mail_template_file: path to the mail template file. Must exist.
    @param subject: email subject
    @param cc_recipients: cc mail recipients
    @param replace_token: format: <token>=<replace-value> - tokens in mail-body are replaced
    '''
    ensure_not_empty(email_cfg_name)

    cfg_factory = ctx().cfg_factory()
    email_cfg = cfg_factory.email(email_cfg_name)

    with open(mail_template_file) as f:
        mail_template = f.read()

    # validate template-tokens
    invalid_tokens = filter(lambda t: not isinstance(t, str) or not '=' in t, replace_token)
    if len(list(invalid_tokens)) > 0:
        fail('all replace-tokens must be of form <key>=<value>: ' + ' '.join(
            invalid_tokens
            )
        )

    # parse replace-tokens
    replace_tokens = dict(map(lambda t: t.split('=', 1), replace_token))

    _send_mail(
        email_cfg=email_cfg,
        recipients=recipients,
        mail_template=mail_template,
        subject=subject,
        cc_recipients=cc_recipients,
        replace_tokens=replace_tokens,
    )


def _send_mail(
    email_cfg: EmailConfig,
    recipients: typing.Iterable[str],
    mail_template: str,
    subject: str,
    replace_tokens: dict={},
    cc_recipients: typing.Iterable[str]=[],
):
    ensure_not_none(email_cfg)
    ensure_not_empty(recipients)
    ensure_not_none(mail_template)
    ensure_not_empty(subject)

    # create body from template
    mail_body = mailer.create_body(
        mail_template=mail_template,
        replace_tokens=replace_tokens,
    )

    # create mail envelope
    mail = mailer.create_mail(
        subject=subject,
        sender=email_cfg.sender_name(),
        recipients=recipients,
        cc_recipients=cc_recipients,
        text=mail_body
    )

    if email_cfg.use_tls():
        smtp_server = smtplib.SMTP_SSL(email_cfg.smtp_host())
    else:
        smtp_server = smtplib.SMTP(email_cfg.smtp_host())

    credentials = email_cfg.credentials()
    smtp_server.login(user=credentials.username(), password=credentials.passwd())

    recipients = set(recipients)
    recipients.update(cc_recipients)

    mailer.send_mail(
        smtp_server=smtp_server,
        msg=mail,
        sender=credentials.username(),
        recipients=recipients
    )


def determine_mail_recipients(src_dir: str):
    recipients = set()

    repo = git.Repo(ensure_directory_exists(src_dir))
    head_commit = repo.commit(repo.head)

    recipients.add(head_commit.author.email.lower())
    recipients.add(head_commit.committer.email.lower())

    return recipients

def notify(src_dir: str, subject: str, body: str, email_cfg_name: str):
    ensure_directory_exists(src_dir)

    recipients = determine_mail_recipients(src_dir=src_dir)
    email_cfg = ctx().cfg_factory().email(email_cfg_name)

    _send_mail(
        email_cfg=email_cfg,
        recipients=recipients,
        mail_template=body,
        subject=subject
    )


