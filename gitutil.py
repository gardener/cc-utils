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

import os
import subprocess
import urllib.parse
from pydash import _

import git

from util import not_empty, not_none, existing_dir, fail, random_str, urljoin


class GitHelper(object):
    def __init__(self, repo, github_cfg, github_repo_path):
        not_none(repo)
        if not isinstance(repo, git.Repo):
            # assume it's a file path if it's not already a git.Repo
            repo = git.Repo(repo)
        self.repo = repo
        self.github_cfg = github_cfg
        self.github_repo_path = github_repo_path

    def _changed_file_paths(self):
        lines = git.cmd.Git(self.repo.working_tree_dir).status('--porcelain=1', '-z').split('\x00')
        # output of git status --porcelain=1 and -z is guaranteed to not change in the future
        return [line[3:] for line in lines if line]

    def _authenticated_remote(self, use_ssh=False):
        if use_ssh:
            url = urljoin(self.github_cfg.ssh_url(), self.github_repo_path)
        else:
            url = url_with_credentials(self.github_cfg, self.github_repo_path)

        remote = git.remote.Remote.add(
            repo=self.repo,
            name=random_str(),
            url=url,
        )
        return remote

    def index_to_commit(self, message, parent_commits=None):
        '''moves all diffs from worktree to a new commit without modifying branches

        @param parent_commits: optional iterable of parent commits; head is used if absent
        @return the git.Commit object representing the newly created commit
        '''
        if not parent_commits:
            parent_commits = [self.repo.head.commit]
        # add all changes
        git.cmd.Git(self.repo.working_tree_dir).add('.')
        tree = self.repo.index.write_tree()
        commit = git.Commit.create_from_tree(
            repo=self.repo,
            tree=tree,
            parent_commits=parent_commits,
            message=message
        )
        self.repo.index.reset()
        return commit

    def _stash_changes(self):
        self.repo.git.stash('--include-untracked', '--quiet')

    def _pop_stash(self):
        self.repo.git.stash('pop', '--quiet')

    def push(self, from_ref, to_ref, use_ssh=False):
        if use_ssh:
            tmp_id = os.path.abspath('tmp.id_rsa')
            with open(tmp_id, 'w') as f:
                f.write(self.github_cfg.credentials().private_key())
            os.chmod(tmp_id, 0o400)
            os.environ['GIT_SSH_COMMAND'] = 'ssh -i {id_file} {suppress_hostcheck} {id_only}'.format(
                id_file=tmp_id,
                suppress_hostcheck='-o "StrictHostKeyChecking no"',
                id_only='-o "IdentitiesOnly yes"',
            )

        remote = self._authenticated_remote(use_ssh=use_ssh)
        try:
            remote.push(':'.join((
                from_ref,
                to_ref,
            ))
            )
            self.repo.delete_remote(remote)
        finally:
            if use_ssh:
                os.unlink(tmp_id)
                del os.environ['GIT_SSH_COMMAND']

    def branch_head(self, branch_name: str):
        remote = _.head(self._authenticated_remote().fetch(branch_name))
        return remote.commit


def clone_repository(
        to_path: str,
        github_cfg,
        github_repo_path: str
    ):
        url = url_with_credentials(github_cfg, github_repo_path)
        git.Git(to_path).clone('--quiet', url)


def url_with_credentials(github_cfg, github_repo_path):
    base_url = urllib.parse.urlparse(github_cfg.http_url())
    credentials = github_cfg.credentials()
    credentials_str = ':'.join((credentials.username(), credentials.passwd()))
    url = urllib.parse.urlunparse((
        base_url.scheme,
        '@'.join((credentials_str, base_url.hostname)),
        github_repo_path,
        '',
        '',
        ''
    ))
    return url


def update_submodule(
    repo_path: str,
    tree_ish: str,
    submodule_path: str,
    commit_hash: str,
    author: str,
    email: str,
):
    '''Update the submodule of a git-repository to a specific commit.

    Create a new commit, with the passed tree-ish as parent, in the given repository.

    Note that this implementation only supports toplevel submodules. To be removed in a
    future version.

    Parameters
    ------
    repo_path : str
        Path to a directory containing an intialised git-repo with a submodule to update.
    tree_ish : str
        Valid tree-ish to use as base for creating the new commit. Used as parent for the
        commit to be created
        Example: 'master' for the head of the master-branch.
    submodule_path : str
        Path (relative to the repository root) to the submodule. Must be immediately below the root
        of the repository.
    commit_hash : str
        The hash the submodule should point to in the created commit. This should be a valid commit-
        hash in the submodule's repository.
    author : str,
        Will be set as author of the created commit
    email : str
        Will be set for the author of the created commit

    Returns
    ------
    str
        The hexadecimal SHA-1 hash of the created commit
    '''
    repo_path = existing_dir(os.path.abspath(repo_path))

    not_empty(submodule_path)
    if '/' in submodule_path:
        fail('This implementation only supports toplevel submodules: {s}'.format(s=submodule_path))

    not_empty(tree_ish)
    not_empty(commit_hash)
    not_empty(author)
    not_empty(email)

    repo = git.Repo(repo_path)
    _ensure_submodule_exists(repo, submodule_path)

    # Create mk-tree-parseable string-representation from given tree-ish.
    tree = repo.tree(tree_ish)
    tree_representation = _serialise_and_update_submodule(tree, submodule_path, commit_hash)

    # Pass the patched tree to git mk-tree using GitPython. We cannot do this in GitPython
    # directly as it does not support arbitrary tree manipulation.
    # We must keep a reference to auto_interrupt as it closes all streams to the subprocess
    # on finalisation
    auto_interrupt = repo.git.mktree(istream = subprocess.PIPE, as_process=True)
    process = auto_interrupt.proc
    stdout, _= process.communicate(input=tree_representation.encode())

    # returned string is byte-encoded and newline-terminated
    new_sha = stdout.decode('utf-8').strip()

    # Create a new commit in the repo's object database from the newly created tree.
    actor = git.Actor(author, email)
    parent_commit = repo.commit(tree_ish)
    commit = git.Commit.create_from_tree(
      repo = repo,
      tree = new_sha,
      parent_commits = [parent_commit],
      message='Upgrade submodule {s} to commit {c}'.format(
          s=submodule_path,
          c=commit_hash,
      ),
      author=actor,
      committer=actor,
    )

    return commit.hexsha


def _serialise_and_update_submodule(
    tree: git.Tree,
    submodule_path: str,
    commit_hash: str,
):
    '''Return a modified, serialised tree-representation in which the given submodule's entry is
    altered such that it points to the specified commit hash.
    The returned serialisation  format is understood by git mk-tree.

    Returns
    ------
        str
            An updated serialised git-tree with the updated submodule entry
    '''
    # GitPython offers no API to retrieve ls-tree representation
    return '\n'.join([
        _serialise_object_replace_submodule(
            tree_element=tree_element,
            submodule_path=submodule_path,
            commit_hash=commit_hash,
        ) for tree_element in tree]
    )


def _serialise_object_replace_submodule(tree_element, submodule_path, commit_hash):
    # GitPython uses the special type 'submodule' for submodules whereas git uses 'commit'.
    if tree_element.type == 'submodule':
        element_type = 'commit'
        # Replace the hash the of the 'commit'-tree with the passed value if the submodule
        # is at the specified path
        if tree_element.path == submodule_path:
            element_sha = commit_hash
    else:
        element_type = tree_element.type
        element_sha = tree_element.hexsha

    return '{mode} {type} {sha}\t{path}'.format(
        sha=element_sha,
        type=element_type,
        # mode is a number in octal representation WITHOUT '0o' prefix
        mode=format(tree_element.mode, 'o'),
        path=tree_element.path,
    )


def _ensure_submodule_exists(repo: git.Repo, path: str):
    '''Use GitPython to verify that a submodule with the given path exists in the repository.'''
    for submodule in repo.submodules:
        if submodule.path == path:
            return
    fail('No submodule with path {p} exists in the repository.'.format(p=path))
