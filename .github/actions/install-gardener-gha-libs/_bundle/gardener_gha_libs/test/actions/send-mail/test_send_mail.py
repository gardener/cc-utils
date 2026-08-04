# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import email.mime.multipart
import os
import sys
import unittest.mock as mock

sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', '..', '..', '.github', 'actions', 'send-mail')
    ),
)

import send_mail


# --- create_mail ---

def test_create_mail_structure():
    msg = send_mail.create_mail(
        subject='Test',
        sender='sender@example.com',
        body='Hello',
        smtp_headers={},
        recipients=['a@example.com'],
        cc_recipients=[],
    )
    assert isinstance(msg, email.mime.multipart.MIMEMultipart)
    assert msg['Subject'] == 'Test'
    assert msg['From'] == 'sender@example.com'
    assert msg['To'] == 'a@example.com'


def test_create_mail_multiple_recipients():
    msg = send_mail.create_mail(
        subject='S',
        sender='s@x.com',
        body='B',
        smtp_headers={},
        recipients=['a@x.com', 'b@x.com'],
        cc_recipients=['c@x.com'],
    )
    assert 'a@x.com' in msg['To']
    assert 'b@x.com' in msg['To']
    assert msg['Cc'] == 'c@x.com'


def test_create_mail_smtp_headers_override():
    msg = send_mail.create_mail(
        subject='Original',
        sender='s@x.com',
        body='B',
        smtp_headers={'Subject': 'Overridden'},
        recipients=['r@x.com'],
        cc_recipients=[],
    )
    # smtp_headers are set after the defaults; the last assignment wins in MIMEMultipart
    assert 'Overridden' in msg.values()


def test_create_mail_attachment(tmp_path):
    att = tmp_path / 'data.txt'
    att.write_text('content')
    msg = send_mail.create_mail(
        subject='S',
        sender='s@x.com',
        body='B',
        smtp_headers={},
        recipients=['r@x.com'],
        cc_recipients=[],
        attachments=[str(att)],
    )
    payloads = msg.get_payload()
    # body + attachment
    assert len(payloads) == 2


def test_create_mail_no_attachment():
    msg = send_mail.create_mail(
        subject='S',
        sender='s@x.com',
        body='B',
        smtp_headers={},
        recipients=['r@x.com'],
        cc_recipients=[],
    )
    payloads = msg.get_payload()
    assert len(payloads) == 1


# --- send_mail recipients normalisation ---

def test_send_mail_lowercases_recipients():
    captured = {}

    def fake_send_email(**kwargs):
        captured['to'] = kwargs['Destination']['ToAddresses']
        return {'MessageId': 'x'}

    mock_client = mock.MagicMock()
    mock_client.send_email.side_effect = fake_send_email

    with mock.patch('send_mail.boto3') as mock_boto3:
        mock_boto3.client.return_value = mock_client
        send_mail.send_mail(
            aws_key_id='key',
            aws_key='secret',
            aws_session_token=None,
            aws_region='eu-central-1',
            subject='S',
            body='B',
            smtp_headers={},
            recipients=['User@Example.COM'],
        )

    assert all(r == r.lower() for r in captured['to'])


def test_send_mail_deduplicates_recipients():
    captured = {}

    def fake_send_email(**kwargs):
        captured['to'] = kwargs['Destination']['ToAddresses']
        return {'MessageId': 'x'}

    mock_client = mock.MagicMock()
    mock_client.send_email.side_effect = fake_send_email

    with mock.patch('send_mail.boto3') as mock_boto3:
        mock_boto3.client.return_value = mock_client
        send_mail.send_mail(
            aws_key_id='key',
            aws_key='secret',
            aws_session_token=None,
            aws_region='eu-central-1',
            subject='S',
            body='B',
            smtp_headers={},
            recipients=['a@x.com', 'A@X.COM'],
        )

    assert len(captured['to']) == 1


def test_send_mail_bcc_included_in_destinations():
    captured = {}

    def fake_send_email(**kwargs):
        captured['to'] = kwargs['Destination']['ToAddresses']
        return {'MessageId': 'x'}

    mock_client = mock.MagicMock()
    mock_client.send_email.side_effect = fake_send_email

    with mock.patch('send_mail.boto3') as mock_boto3:
        mock_boto3.client.return_value = mock_client
        send_mail.send_mail(
            aws_key_id='key',
            aws_key='secret',
            aws_session_token=None,
            aws_region='eu-central-1',
            subject='S',
            body='B',
            smtp_headers={},
            recipients=['to@x.com'],
            bcc_recipients=['bcc@x.com'],
        )

    assert 'bcc@x.com' in captured['to']


def test_send_mail_truncates_at_max_recipients():
    called_with = {}

    def fake_send_email(**kwargs):
        called_with['to'] = kwargs['Destination']['ToAddresses']
        return {'MessageId': 'x'}

    mock_client = mock.MagicMock()
    mock_client.send_email.side_effect = fake_send_email

    recipients = [f'user{i}@x.com' for i in range(60)]
    with mock.patch('send_mail.boto3') as mock_boto3:
        mock_boto3.client.return_value = mock_client
        send_mail.send_mail(
            aws_key_id='key',
            aws_key='secret',
            aws_session_token=None,
            aws_region='eu-central-1',
            subject='S',
            body='B',
            smtp_headers={},
            recipients=recipients,
            max_recipients=50,
        )

    assert len(called_with['to']) == 50


# --- authenticate_against_aws ---

def test_authenticate_against_aws(monkeypatch):
    monkeypatch.setenv('ACTIONS_ID_TOKEN_REQUEST_TOKEN', 'gh-tok')
    monkeypatch.setenv('ACTIONS_ID_TOKEN_REQUEST_URL', 'https://token.example.com')

    mock_session = mock.MagicMock()
    mock_session.get.return_value.json.return_value = {'value': 'oidc-tok'}
    mock_session.post.return_value.json.return_value = {
        'AssumeRoleWithWebIdentityResponse': {
            'AssumeRoleWithWebIdentityResult': {
                'Credentials': {
                    'AccessKeyId': 'AKID',
                    'SecretAccessKey': 'SECRET',
                    'SessionToken': 'TOKEN',
                },
            },
        },
    }

    with mock.patch('send_mail.requests.Session', return_value=mock_session):
        key_id, key, token = send_mail.authenticate_against_aws(
            role_to_assume='arn:aws:iam::123:role/Foo',
        )

    assert key_id == 'AKID'
    assert key == 'SECRET'
    assert token == 'TOKEN'


def test_authenticate_against_aws_missing_env_exits(monkeypatch):
    monkeypatch.delenv('ACTIONS_ID_TOKEN_REQUEST_TOKEN', raising=False)
    monkeypatch.delenv('ACTIONS_ID_TOKEN_REQUEST_URL', raising=False)

    with mock.patch('send_mail.exit') as mock_exit:
        mock_exit.side_effect = SystemExit(1)
        try:
            send_mail.authenticate_against_aws(role_to_assume='arn:...')
        except SystemExit:
            pass
    mock_exit.assert_called_once_with(1)
