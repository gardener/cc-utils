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

from email.mime.text import MIMEText


def create_body(mail_template: str, replace_tokens: dict):
    for key, value in replace_tokens.items():
        mail_template = mail_template.replace(key, value)

    return mail_template


def send_mail(smtp_server: str, msg: str, sender: str, recipients: str):
    smtp_server.sendmail(sender, recipients, msg.as_string())


def create_mail(
        subject: str,
        sender: str,
        recipients: [str],
        text: str,
        cc_recipients: [str]=[],
        mail_type: str='plain'
    )->MIMEText:
    msg = MIMEText(text, mail_type)
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = ';'.join(recipients)
    msg['Cc'] = ';'.join(cc_recipients)

    return msg

