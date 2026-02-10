#!/usr/bin/env python3

import argparse
import collections.abc
import email.mime.application
import email.mime.multipart
import email.mime.text
import enum
import os
import string

import boto3
import yaml


class MimeType(enum.StrEnum):
    TEXT = 'text'


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        '--aws-key-id',
        required=True,
    )
    parser.add_argument(
        '--aws-key',
        required=True,
    )
    parser.add_argument(
        '--aws-region',
        required=False,
        default='eu-central-1',
    )
    parser.add_argument(
        '--subject',
        required=True,
    )
    parser.add_argument(
        '--body',
        required=False,
        default=None,
    )
    parser.add_argument(
        '--body-file',
        required=False,
        default=None,
    )
    parser.add_argument(
        '--smtp-headers',
        required=False,
        default=None,
    )
    parser.add_argument(
        '--recipients',
        action='append',
        required=True,
    )
    parser.add_argument(
        '--cc-recipients',
        action='append',
        required=False,
        default=None,
    )
    parser.add_argument(
        '--bcc-recipients',
        action='append',
        required=False,
        default=None,
    )
    parser.add_argument(
        '--attachments',
        action='append',
        required=False,
        default=None,
    )
    parser.add_argument(
        '--build-job-url',
        required=False,
        default=None,
    )

    return parser.parse_args()


def write_to_gha_summary(message: str):
    if not 'GITHUB_STEP_SUMMARY' in os.environ:
        return # not running in GHA context -> ignore for now

    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
        f.write(f'{message}\n')


def create_mail(
    subject: str,
    sender: str,
    body: str,
    smtp_headers: dict,
    recipients: collections.abc.Iterable[str],
    cc_recipients: collections.abc.Iterable[str],
    attachments: collections.abc.Iterable[str] | None=None,
    mimetype: MimeType=MimeType.TEXT,
) -> email.mime.multipart.MIMEMultipart:
    msg = email.mime.multipart.MIMEMultipart('alternative')

    if mimetype is MimeType.TEXT:
        msg_plain = email.mime.text.MIMEText(
            body,
            mimetype,
            'utf-8',
        )
    else:
        print(f'ERROR: Unsupported {mimetype=}')
        exit(1)

    msg.attach(msg_plain)

    for attachment in (attachments or []):
        filename = os.path.basename(attachment)
        with open(attachment, 'rb') as f:
            attachment = email.mime.application.MIMEApplication(
                f.read(),
                Name=filename,
            )
        attachment['Content-Disposition'] = f'attachment; filename="{filename}"'
        msg.attach(attachment)

    smtp_headers = {
        'Subject': subject,
        'From': sender,
        'To': ','.join(recipients),
        'Cc': ','.join(cc_recipients),
        **smtp_headers,
    }

    for key, value in smtp_headers.items():
        msg[key] = value

    return msg


def send_mail(
    aws_key_id: str,
    aws_key: str,
    aws_region: str,
    subject: str,
    body: str,
    smtp_headers: dict,
    recipients: collections.abc.Iterable[str],
    cc_recipients: collections.abc.Iterable[str] | None=None,
    bcc_recipients: collections.abc.Iterable[str] | None=None,
    attachments: collections.abc.Iterable[str] | None=None,
    sender: str='Gardener GHA <gha@gardener.cloud.sap>',
    mimetype: MimeType=MimeType.TEXT,
    max_recipients: int=50,
):
    recipients = {recipient.lower() for recipient in recipients}

    if cc_recipients:
        cc_recipients = {cc_recipient.lower() for cc_recipient in cc_recipients}
    else:
        cc_recipients = set()

    if bcc_recipients:
        bcc_recipients = {bcc_recipient.lower() for bcc_recipient in bcc_recipients}
    else:
        bcc_recipients = set()

    mail = create_mail(
        subject=subject,
        sender=sender,
        body=body,
        smtp_headers=smtp_headers,
        recipients=recipients,
        cc_recipients=cc_recipients,
        attachments=attachments,
        mimetype=mimetype,
    )

    recipients.update(cc_recipients)
    recipients.update(bcc_recipients)
    recipients = list(recipients)

    write_to_gha_summary('## Email Notification')

    if len(recipients) > max_recipients:
        msg = f'WARNING: Maximum recipients exceeded, will limit to {max_recipients}'
        print(msg)
        write_to_gha_summary(msg)

        recipients = recipients[:50]

    client = boto3.client(
        'sesv2',
        aws_access_key_id=aws_key_id,
        aws_secret_access_key=aws_key,
        region_name=aws_region,
    )

    response = client.send_email(
        FromEmailAddress=sender,
        Destination={
            'ToAddresses': recipients,
        },
        Content={
            'Raw': {
                'Data': str(mail).encode(),
            },
        },
    )
    print(response)

    msg = f'INFO: Sent email to: {recipients}'
    print(msg)
    write_to_gha_summary(msg)


def main():
    parsed_args = parse_args()

    aws_key_id = parsed_args.aws_key_id
    aws_key = parsed_args.aws_key

    if not (bool(parsed_args.body) ^ bool(parsed_args.body_file)):
        print('Usage: Exactly one of `--body` and `--body-file` must be passed')
        exit(1)
    elif not (body := parsed_args.body):
        with open(parsed_args.body_file) as f:
            body = f.read().strip()

    template_context = {}
    if build_job_url := parsed_args.build_job_url:
        template_context['build_job_url'] = build_job_url

    body = string.Template(body).substitute(template_context)

    if parsed_args.smtp_headers:
        smtp_headers = yaml.safe_load(parsed_args.smtp_headers)
    else:
        smtp_headers = {}

    send_mail(
        aws_key_id=aws_key_id,
        aws_key=aws_key,
        aws_region=parsed_args.aws_region,
        subject=parsed_args.subject,
        body=body,
        smtp_headers=smtp_headers,
        recipients=parsed_args.recipients,
        cc_recipients=parsed_args.cc_recipients,
        bcc_recipients=parsed_args.bcc_recipients,
        attachments=parsed_args.attachments,
    )


if __name__ == '__main__':
    main()
