import os
import subprocess

import git

from util import ensure_not_empty, ensure_directory_exists

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

    Parameters
    ------
    repo_path : str
        Path to a directory containing an intialised git-repo with a submodule to update.
    tree_ish : str
        Valid tree-ish to use as base for creating the new commit. This will be used as parent for the commit created by this function.
        Example: 'master' for the head of the master-branch.
    submodule_path : str
        Path (relative to `repo_path`) to the submodule. Must be immediately below the root (i.e. `repo_path`) of the repository.
    commit_hash : str
        The hash the submodule should point to in the created commit. This should be a valid commit hash in the submodule's repository.
    author : str,
        Will be set as author of the created commit
    email : str
        Will be set for the author of the created commit

    Returns
    ------
    str
        The hexadecimal SHA-1 hash of the created commit
    '''
    ensure_directory_exists(repo_path)
    repo_path = os.path.abspath(repo_path)

    ensure_not_empty(tree_ish)
    ensure_not_empty(submodule_path)
    ensure_directory_exists(os.path.join(repo_path, submodule_path))
    ensure_not_empty(commit_hash)
    ensure_not_empty(author)
    ensure_not_empty(email)

    repo = git.Repo(repo_path)

    # Fetch tree-ish from git.repo and create mktree-parseable string-representation of it.
    # Since submodule-objects contain the SHA of the to-be-checked-out version of the
    # target repository, we replace that during the string building.
    tree = repo.tree(tree_ish)
    tree_representation = '\n'.join(_tree_string_generator(tree, submodule_path, commit_hash))

    # Pass the serialised trees to git mk-tree using GitPython. We cannot do this in GitPython
    # directly as it does not support arbitrary tree manipulation.
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


def _tree_string_generator(
    tree: git.Tree,
    submodule_path: str,
    commit_hash: str,
):
    '''Create a generator that yields the ls-tree string-representation of the passed tree one line at a time.
    Replaces the commit-hash of the tree element with the given one if it is a submodule and its submodule
    path matches the passed `submodule_path`.
    '''
    # Since there is no direct way to get the ls-tree representation from GitPython we need to gather
    # the necessary information for each element of the given tree ourselves.
    for tree_element in tree:
      # GitPython uses the special type 'submodule' for submodules whereas git uses 'commit'.
      if tree_element.type == 'submodule':
          element_type = 'commit'
          # Replace the hash the of the 'commit'-tree with the passed value if the submodule is at the specified path
          if tree_element.path == submodule_path:
              element_sha = commit_hash
      else:
          entry_type = tree_element.type
          element_sha = tree_element.hexsha

      yield '{mode} {type} {sha}\t{path}'.format(
        sha=element_sha,
        type=element_type,
        # mode is a number in octal representation WITHOUT '0o' prefix
        mode=format(tree_element.mode, 'o'),
        path=tree_element.path,
      )
