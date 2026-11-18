# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

import base64
import unittest.mock

import pytest

import github.commit as gc


def _mock_session(response_json, status_raises=None):
    '''returns (session_mock, captured) where captured records the post() call kwargs.'''
    captured = {}
    response = unittest.mock.Mock()
    response.json.return_value = response_json
    if status_raises:
        response.raise_for_status.side_effect = status_raises

    def post(url, json=None, **kwargs):
        captured['url'] = url
        captured['json'] = json
        return response

    session = unittest.mock.Mock()
    session.post.side_effect = post
    return session, captured


def _patch_api(session):
    api = unittest.mock.Mock()
    api.session = session
    return unittest.mock.patch.object(gc.github, 'github_api', return_value=api)


def test_filechange_validation():
    with pytest.raises(ValueError):
        gc.FileChange(path='x', delete=True, content=b'nope')
    with pytest.raises(ValueError):
        gc.FileChange(path='x')  # neither delete nor content


def test_payload_shape_additions_and_deletions():
    ok = {'data': {'createCommitOnBranch': {'commit': {'oid': 'deadbeef'}}}}
    session, captured = _mock_session(ok)
    with _patch_api(session):
        oid = gc.create_signed_commit(
            token='t',
            repository='gardener/cc-utils',
            branch='master',
            expected_head_oid='cafe1234',
            headline='next version: 1.2.3-dev',
            changes=[
                gc.FileChange(path='VERSION', content=b'1.2.3-dev\n'),
                gc.FileChange(path='.ocm/release-notes/old.yaml', delete=True),
            ],
        )

    assert oid == 'deadbeef'
    body = captured['json']
    assert 'createCommitOnBranch' in body['query']
    inp = body['variables']['input']
    assert inp['branch'] == {
        'repositoryNameWithOwner': 'gardener/cc-utils',
        'branchName': 'master',
    }
    assert inp['expectedHeadOid'] == 'cafe1234'
    assert inp['message'] == {'headline': 'next version: 1.2.3-dev', 'body': ''}

    additions = inp['fileChanges']['additions']
    assert additions == [{
        'path': 'VERSION',
        'contents': base64.b64encode(b'1.2.3-dev\n').decode('ascii'),
    }]
    assert inp['fileChanges']['deletions'] == [{'path': '.ocm/release-notes/old.yaml'}]


def test_binary_content_is_base64():
    ok = {'data': {'createCommitOnBranch': {'commit': {'oid': 'x'}}}}
    session, captured = _mock_session(ok)
    binary = b'\xff\xfe\x00\x01'
    with _patch_api(session):
        gc.create_signed_commit(
            token='t', repository='o/r', branch='b', expected_head_oid='p',
            headline='m', changes=[gc.FileChange(path='bin', content=binary)],
        )
    contents = captured['json']['variables']['input']['fileChanges']['additions'][0]['contents']
    assert base64.b64decode(contents) == binary


def test_graphql_errors_raise():
    err = {'errors': [{'message': 'Expected branch to point to "abc"'}]}
    session, _ = _mock_session(err)
    with _patch_api(session):
        with pytest.raises(RuntimeError, match='createCommitOnBranch failed'):
            gc.create_signed_commit(
                token='t', repository='o/r', branch='b', expected_head_oid='p',
                headline='m', changes=[gc.FileChange(path='a', content=b'x')],
            )


def test_empty_changes_rejected():
    session, _ = _mock_session({})
    with _patch_api(session):
        with pytest.raises(ValueError):
            gc.create_signed_commit(
                token='t', repository='o/r', branch='b', expected_head_oid='p',
                headline='m', changes=[],
            )
    # must not even attempt a POST
    session.post.assert_not_called()


def test_endpoint_defaults_to_github_com():
    assert gc._graphql_endpoint(None) == 'https://api.github.com/graphql'
    assert gc._graphql_endpoint('github.com') == 'https://api.github.com/graphql'


def test_endpoint_for_ghes():
    assert gc._graphql_endpoint('github.enterprise.corp') \
        == 'https://github.enterprise.corp/api/graphql'


def test_endpoint_is_used_for_post():
    ok = {'data': {'createCommitOnBranch': {'commit': {'oid': 'x'}}}}
    session, captured = _mock_session(ok)
    with _patch_api(session):
        gc.create_signed_commit(
            token='t', repository='o/r', branch='b', expected_head_oid='p', headline='m',
            host='github.enterprise.corp',
            changes=[gc.FileChange(path='a', content=b'x')],
        )
    assert captured['url'] == 'https://github.enterprise.corp/api/graphql'
