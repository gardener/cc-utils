# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

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


def sign_commit_onto_branch(
    *,
    repo_dir: str,
    token: str,
    repository_full_name: str,
    branch: str,
    expected_head_oid: str,
    message: str,
    host: str | None = None,
) -> str:
    '''
    stages the current worktree-changes and creates a GitHub-signed commit onto `branch` (which
    must already exist at `expected_head_oid`) via `createCommitOnBranch`. Returns the signed
    commit-oid.

    Raises ValueError if `token` is falsy or the worktree has no changes against
    `expected_head_oid` (an upgrade-PR must never create an empty commit).
    '''
    if not token:
        raise ValueError('token must not be empty')

    cwd = os.getcwd()
    os.chdir(repo_dir)
    try:
        changes = signed_commit.changes_from_worktree(parent_sha=expected_head_oid)
    finally:
        os.chdir(cwd)

    if not changes:
        raise ValueError('refusing to create an empty upgrade-commit (no worktree-changes)')

    headline, _, body = message.partition('\n\n')

    return signed_commit.create_signed_commit(
        token=token,
        repository=repository_full_name,
        branch=branch,
        expected_head_oid=expected_head_oid,
        headline=headline,
        body=body,
        changes=changes,
        host=host,
    )
