#!/usr/bin/env python

import argparse
import os
import time

import jwt
import requests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--client-id',
        required=True,
        help="GitHub-App's client-id (or app-id)",
    )
    parser.add_argument(
        '--private-key',
        required=True,
        help="GitHub-App's private-key in PEM-format",
    )
    parser.add_argument(
        '--github-api',
        required=False,
        default=os.environ.get('GITHUB_API_URL', None),
    )
    parser.add_argument(
        '--github-org',
        required=True,
    )
    parser.add_argument(
        '--repository',
        dest='repositories',
        required=False,
        default=[],
        action='append',
    )

    p = parser.parse_args()

    now = int(time.time())
    payload = {
        'iat': now,
        'exp': now + 600, # 10m
        'iss': p.client_id,
    }

    encoded_jwt = jwt.encode(payload, p.private_key, algorithm='RS256')

    sess = requests.Session()
    sess.headers['Authorization'] = f'Bearer {encoded_jwt}'
    sess.headers['Accept'] = 'application/vnd.github+json'

    def api_url(suffix):
        return f'{p.github_api}/{suffix}'

    installation = sess.get(api_url(f'orgs/{p.github_org}/installation')).json()
    installation_id = installation.get('id')

    if p.repositories:
        body = {
            'repositories': p.repositories,
        }
    else:
        body = None

    access_token = sess.post(
        api_url(f'app/installations/{installation_id}/access_tokens'),
        json=body,
    )
    access_token.raise_for_status()

    print(access_token.json()['token'])


if __name__ == '__main__':
    main()
