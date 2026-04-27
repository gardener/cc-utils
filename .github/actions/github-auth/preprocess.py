#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

'''
Preprocess inputs for the github-auth action.

Replaces the bash preprocess step to avoid jq/yq tool dependencies. Accepts
all values as environment variables (set by the caller) and writes results to
GITHUB_OUTPUT.

Environment variables consumed:
  INPUT_HOST            - optional host override
  INPUT_ORGANIZATION    - optional org override
  INPUT_REPOSITORIES    - optional newline-separated list of repositories
  INPUT_PERMISSIONS     - YAML permissions block
  DEFAULT_HOST          - github.server_url (used when INPUT_HOST is empty)
  DEFAULT_ORGANIZATION  - github.repository_owner (used when INPUT_ORGANIZATION is empty)
'''

import json
import os

import yaml


def preprocess(
    host: str,
    organization: str,
    repositories_raw: str,
    permissions_raw: str,
    default_host: str,
    default_organization: str,
) -> dict:
    '''
    Returns a dict with keys: host, organization, repositories (JSON string),
    permissions (JSON string).
    '''
    # strip scheme prefix from host (https://github.com -> github.com)
    resolved_host = (host or default_host).split('://', 1)[-1]
    resolved_org = organization or default_organization

    repositories = json.dumps([r for r in repositories_raw.splitlines() if r.strip()])
    permissions = json.dumps(yaml.safe_load(permissions_raw) or {})

    return {
        'host': resolved_host,
        'organization': resolved_org,
        'repositories': repositories,
        'permissions': permissions,
    }


if __name__ == '__main__':
    result = preprocess(
        host=os.environ.get('INPUT_HOST', ''),
        organization=os.environ.get('INPUT_ORGANIZATION', ''),
        repositories_raw=os.environ.get('INPUT_REPOSITORIES', ''),
        permissions_raw=os.environ.get('INPUT_PERMISSIONS', 'contents: read\n'),
        default_host=os.environ.get('DEFAULT_HOST', ''),
        default_organization=os.environ.get('DEFAULT_ORGANIZATION', ''),
    )

    with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
        for key, value in result.items():
            f.write(f'{key}={value}\n')
