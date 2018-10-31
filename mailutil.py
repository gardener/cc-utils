# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
# under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

from model.email import EmailConfig
from util import (
    existing_dir,
    not_empty,
    not_none,
    info,
    fail,
    CliHint,
    ctx,
    CliHints,
)
from mail import template_mailer as mailer
import github.util
from github.codeowners import CodeownersParser, CodeOwnerEntryResolver
import product.model


def send_mail(
    email_cfg_name: CliHint(help="reference to an email cfg (see repo cc-config / secrets-server)"),
    recipients: CliHint(typehint=[str], help="Recipient email address"),
    mail_template_file: CliHints.existing_file(),
    subject: CliHint(help="email subject"),
    cc_recipients: CliHint(typehint=[str], help="Carbon copy email address")=[],
    replace_token: CliHint(typehint=[str], help="<key>=<value> (replace <key> in body)")=[],
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
    not_empty(email_cfg_name)

    cfg_factory = ctx().cfg_factory()
    email_cfg = cfg_factory.email(email_cfg_name)

    with open(mail_template_file) as f:
        mail_template = f.read()

    # validate template-tokens
    invalid_tokens = filter(lambda t: not isinstance(t, str) or '=' not in t, replace_token)
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
    not_none(email_cfg)
    not_empty(recipients)
    not_none(mail_template)
    not_empty(subject)

    # create body from template
    mail_body = mailer.create_body(
        mail_template=mail_template,
        replace_tokens=replace_tokens,
    )

    recipients = set(map(str.lower, recipients))
    cc_recipients = set(map(str.lower, cc_recipients))

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

    recipients.update(cc_recipients)

    mailer.send_mail(
        smtp_server=smtp_server,
        msg=mail,
        sender=credentials.username(),
        recipients=recipients
    )


def determine_mail_recipients(
    github_cfg_name,
    src_dirs=(),
    component_names=(),
    branch_name='master',
):
    '''
    returns a generator yielding all email addresses for the given (git) repository work tree
    Email addresses are looked up:
    - from head commit: author and committer
    - from *CODEOWNERS files [0]

    Email addresses are not de-duplicated (this should be done by consumers)

    [0] https://help.github.com/articles/about-codeowners/
    '''
    if not component_names and not src_dirs:
        return # nothing to do

    cfg_factory = ctx().cfg_factory()

    github_cfg = cfg_factory.github(github_cfg_name)
    github_api = github.util._create_github_api_object(github_cfg)
    resolver = CodeOwnerEntryResolver(github_api=github_api)

    for src_dir in src_dirs:
        # commiter/author from head commit
        repo = git.Repo(existing_dir(src_dir))
        head_commit = repo.commit(repo.head)
        yield head_commit.author.email.lower()
        yield head_commit.committer.email.lower()

    # collect parsers
    parsers = [
        _codeowners_parser_from_repo_worktree(src_dir=src_dir)
        for src_dir in src_dirs
    ]
    parsers += [
        _codeowners_parser_from_component_name(
            component_name=component_name,
            branch_name=branch_name
        ) for component_name in component_names
    ]

    for parser in parsers:
        codeowner_entries = parser.parse_codeowners_entries()
        yield from resolver.resolve_email_addresses(codeowner_entries)


def _codeowners_parser_from_repo_worktree(src_dir):
    return CodeownersParser(repo_dir=src_dir)


def _codeowners_parser_from_component_name(component_name: str, branch_name='master'):
    component_name = product.model.ComponentName(component_name)
    github_cfg = github.util.github_cfg_for_hostname(
        cfg_factory=ctx().cfg_factory(),
        host_name=component_name.github_host(),
    )
    github_api = github.util._create_github_api_object(github_cfg=github_cfg)

    github_repo_helper = github.util.GitHubRepositoryHelper(
        owner=component_name.github_organisation(),
        name=component_name.github_repo(),
        default_branch=branch_name,
        github_api=github_api,
    )

    return CodeownersParser(github_repo_helper=github_repo_helper)


def notify(
    subject: str,
    body: str,
    email_cfg_name: str,
    recipients: typing.Iterable[str],
):
    recipients = set(recipients)
    cfg_factory = ctx().cfg_factory()

    email_cfg = cfg_factory.email(email_cfg_name)

    _send_mail(
        email_cfg=email_cfg,
        recipients=recipients,
        mail_template=body,
        subject=subject
    )
    info('sent email to: {r}'.format(r=recipients))
