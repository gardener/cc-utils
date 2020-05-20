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
        recipients: [str],
        text: str,
        cc_recipients: [str]=[],
        mimetype: str='text'
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
