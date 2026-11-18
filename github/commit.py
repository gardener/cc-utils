# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

'''
helpers for creating GitHub-signed ("Verified") commits via the GraphQL
`createCommitOnBranch` mutation.

A commit only receives GitHub's signature when the commit-object is authored server-side while
authenticated using an app-/installation-token. There is no signing-key on our side — GitHub
signs with its own key. GitHub's recommended way to author such a commit is the
`createCommitOnBranch` mutation, which commits onto an existing branch in a single call and
returns the resulting (signed) commit-oid.

Caveat: `createCommitOnBranch` has no per-file mode field. File-modes are inherited from the tree
at `expected_head_oid`. This is fine for our use-cases (version-text edits + file deletions). A
commit that needs to introduce/alter an executable-bit or symlink cannot be expressed this way.
'''

import base64
import collections.abc
import dataclasses

import github


_MUTATION = '''
mutation ($input: CreateCommitOnBranchInput!) {
  createCommitOnBranch(input: $input) {
    commit { oid }
  }
}
'''


@dataclasses.dataclass
class FileChange:
    '''
    a single file-change to include in a commit. `content` holds the new file-contents (bytes).
    It must be `None` iff `delete` is set.
    '''
    path: str
    content: bytes | None = None
    delete: bool = False

    def __post_init__(self):
        if self.delete and self.content is not None:
            raise ValueError(f'{self.path=}: must not pass content for a deletion')
        if not self.delete and self.content is None:
            raise ValueError(f'{self.path=}: content is required unless delete is set')


def _graphql_endpoint(host: str | None) -> str:
    '''
    returns the GraphQL-endpoint for the given GitHub host. Defaults to github.com. For
    GitHub-Enterprise-Server the endpoint is `https://<host>/api/graphql` (note: NOT `/api/v3`,
    which is the REST-endpoint).
    '''
    if not host or host == 'github.com':
        return 'https://api.github.com/graphql'
    return f'https://{host}/api/graphql'


def create_signed_commit(
    *,
    token: str,
    repository: str,
    branch: str,
    expected_head_oid: str,
    headline: str,
    body: str='',
    changes: collections.abc.Iterable[FileChange],
    host: str | None=None,
) -> str:
    '''
    creates a GitHub-signed commit on `branch` via the `createCommitOnBranch` GraphQL-mutation and
    returns the resulting (signed) commit-oid. The branch-ref is advanced to the new commit as
    part of the mutation (atomic).

    @param repository: `<org>/<repo>`
    @param branch: the (unqualified) branch-name to commit onto. Must already exist.
    @param expected_head_oid: the oid the branch is expected to be at. The mutation is rejected if
        the branch has advanced (optimistic-concurrency / race-guard).
    @param changes: the file-changes (adds/modifications/deletions) to include.
    @param host: GitHub host (defaults to github.com). Used to derive the GraphQL-endpoint.
    '''
    changes = list(changes)
    if not changes:
        raise ValueError('refusing to create a commit without any file-changes')

    additions = [
        {
            'path': change.path,
            'contents': base64.b64encode(change.content).decode('ascii'),
        }
        for change in changes if not change.delete
    ]
    deletions = [{'path': change.path} for change in changes if change.delete]

    variables = {
        'input': {
            'branch': {
                'repositoryNameWithOwner': repository,
                'branchName': branch,
            },
            'expectedHeadOid': expected_head_oid,
            'message': {
                'headline': headline,
                'body': body,
            },
            'fileChanges': {
                'additions': additions,
                'deletions': deletions,
            },
        },
    }

    # reuse github3's authenticated session (carries auth-header, user-agent, retry-config).
    # host is passed explicitly to derive the GraphQL-endpoint, so we do not rely on parsing
    # `repository` (which is <org>/<repo>, not a full host/org/repo url).
    session = github.github_api(token=token).session
    response = session.post(
        _graphql_endpoint(host),
        json={'query': _MUTATION, 'variables': variables},
    )
    response.raise_for_status()
    payload = response.json()

    if payload.get('errors'):
        raise RuntimeError(f'createCommitOnBranch failed: {payload["errors"]}')

    return payload['data']['createCommitOnBranch']['commit']['oid']
