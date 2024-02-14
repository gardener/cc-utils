# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from html2text import html2text


def create_body(mail_template: str, replace_tokens: dict):
    for key, value in replace_tokens.items():
        mail_template = mail_template.replace(key, value)

    return mail_template


def create_mail(
        subject: str,
        sender: str,
        recipients: list[str],
        text: str,
        cc_recipients: list[str]=(),
        mimetype: str='text',
    ) -> MIMEMultipart:
    msg = MIMEMultipart('alternative')

    if mimetype == 'html':
        msg_html = MIMEText(
            text,
            mimetype,
            'utf-8',
        )
        plain_text = html2text(text)
        msg_plain = MIMEText(
            plain_text,
            'text',
            'utf-8',
        )
    elif mimetype == 'text':
        msg_html = None
        msg_plain = MIMEText(
            text,
            mimetype,
            'utf-8',
        )
    else:
        raise NotImplementedError()

    msg.attach(msg_plain)
    if msg_html:
        msg.attach(msg_html)

    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = ','.join(recipients)
    msg['Cc'] = ','.join(cc_recipients)

    return msg
