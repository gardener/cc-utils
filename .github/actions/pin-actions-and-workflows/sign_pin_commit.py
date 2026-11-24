# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

'''
helper for `pin.py`: creates ONE GitHub-signed commit (a single file) via `createCommitOnBranch`
onto an existing branch and returns the signed oid.

Loads `signed_commit` from the sibling action-path (`../signed-commit/signed_commit.py`) via
`spec_from_file_location` rather than importing it: both scripts are loaded from the pinned
action-ref (`GITHUB_ACTION_PATH`), and the helper must not be taken from the installed
`gardener-gha-libs` bundle (built from github.com master on GHE). See `sign_upgrade_commit.py`
for the same reasoning.
'''

import importlib.util
import os

_SIGNED_COMMIT_SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    os.pardir, 'signed-commit', 'signed_commit.py',
)


def _load_signed_commit():
    spec = importlib.util.spec_from_file_location('signed_commit', _SIGNED_COMMIT_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


signed_commit = _load_signed_commit()


def sign_file_commit(
    *,
    token: str,
    repository_full_name: str,
    branch: str,
    expected_head_oid: str,
    path: str,
    content: bytes,
    message: str,
    host: str | None = None,
) -> str:
    '''
    creates a signed commit on `branch` (which must be at `expected_head_oid`) that sets exactly
    `path` to `content`, and returns the signed commit-oid.

    `message` is split at the first `\\n\\n` into headline/body (like a git commit-message).
    Raises ValueError on empty `token`.
    '''
    if not token:
        raise ValueError('token must not be empty')

    headline, _, body = message.partition('\n\n')

    return signed_commit.create_signed_commit(
        token=token,
        repository=repository_full_name,
        branch=branch,
        expected_head_oid=expected_head_oid,
        headline=headline,
        body=body,
        changes=[signed_commit.FileChange(path=path, content=content)],
        host=host,
    )
