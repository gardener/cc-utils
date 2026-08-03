# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import json
import os
import sys

sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', '..', '..', '.github', 'actions',
                     'github-auth')
    ),
)

import preprocess


def test_repositories_parsed_to_json_array():
    result = preprocess.preprocess(
        host='',
        organization='',
        repositories_raw='repo1\nrepo2\nrepo3\n',
        permissions_raw='contents: read\n',
        default_host='https://github.com',
        default_organization='my-org',
    )
    repos = json.loads(result['repositories'])
    assert repos == ['repo1', 'repo2', 'repo3']


def test_empty_lines_filtered_from_repositories():
    result = preprocess.preprocess(
        host='',
        organization='',
        repositories_raw='\n  \nrepo1\n\nrepo2\n',
        permissions_raw='contents: read\n',
        default_host='https://github.com',
        default_organization='my-org',
    )
    repos = json.loads(result['repositories'])
    assert repos == ['repo1', 'repo2']


def test_permissions_yaml_to_compact_json():
    result = preprocess.preprocess(
        host='',
        organization='',
        repositories_raw='',
        permissions_raw='contents: read\nissues: write\n',
        default_host='https://github.com',
        default_organization='my-org',
    )
    perms = json.loads(result['permissions'])
    assert perms == {'contents': 'read', 'issues': 'write'}


def test_host_defaults_to_server_url_without_scheme():
    result = preprocess.preprocess(
        host='',
        organization='',
        repositories_raw='',
        permissions_raw='contents: read\n',
        default_host='https://github.com',
        default_organization='my-org',
    )
    assert result['host'] == 'github.com'


def test_explicit_host_strips_scheme():
    result = preprocess.preprocess(
        host='https://ghe.example.com',
        organization='',
        repositories_raw='',
        permissions_raw='contents: read\n',
        default_host='https://github.com',
        default_organization='my-org',
    )
    assert result['host'] == 'ghe.example.com'


def test_organization_defaults_to_repo_owner():
    result = preprocess.preprocess(
        host='',
        organization='',
        repositories_raw='',
        permissions_raw='contents: read\n',
        default_host='https://github.com',
        default_organization='fallback-org',
    )
    assert result['organization'] == 'fallback-org'


def test_explicit_organization_used_over_default():
    result = preprocess.preprocess(
        host='',
        organization='explicit-org',
        repositories_raw='',
        permissions_raw='contents: read\n',
        default_host='https://github.com',
        default_organization='fallback-org',
    )
    assert result['organization'] == 'explicit-org'
