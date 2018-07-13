# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

import git

from util import not_empty, existing_dir, fail

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
        Valid tree-ish to use as base for creating the new commit. This will be used as parent for the
        commit created by this function.
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
    # Since there is no direct way to get the ls-tree representation from GitPython we need to gather
    # the necessary information for each element of the given tree ourselves.
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
        # Replace the hash the of the 'commit'-tree with the passed value if the submodule is at the specified path
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
