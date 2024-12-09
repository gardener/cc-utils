# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import contextlib
import functools
import logging
import os
import tempfile
import urllib.parse

import git
import git.objects.util
import git.remote

import ci.log
from ci.util import not_none, random_str, urljoin
from model.github import (
    GithubConfig,
    Protocol,
)

logger = logging.getLogger(__name__)
ci.log.configure_default_logging()


def _ssh_auth_env(github_cfg):
    credentials = github_cfg.credentials()
    logger.info(f'using github-credentials with {credentials.username()=}')

    tmp_id = tempfile.NamedTemporaryFile(mode='w', delete=False) # noqa; callers must unlink
    tmp_id.write(credentials.private_key())
    tmp_id.flush()

    os.chmod(tmp_id.name, 0o400)
    suppress_hostcheck = '-o "StrictHostKeyChecking no"'
    id_only = '-o "IdentitiesOnly yes"'
    cmd_env = os.environ.copy()
    cmd_env['GIT_SSH_COMMAND'] = f'ssh -v -i {tmp_id.name} {suppress_hostcheck} {id_only}'
    return (cmd_env, tmp_id)


class GitHelper:
    def __init__(self, repo, github_cfg: GithubConfig, github_repo_path):
        not_none(repo)
        if not isinstance(repo, git.Repo):
            # assume it's a file path if it's not already a git.Repo
            repo = git.Repo(str(repo))
        self.repo = repo
        self.github_cfg = github_cfg
        self.github_repo_path = github_repo_path

    @staticmethod
    def clone_into(
        target_directory: str,
        github_cfg: GithubConfig,
        github_repo_path: str,
        checkout_branch: str = None,
    ) -> 'GitHelper':

        protocol = github_cfg.preferred_protocol()
        if protocol is Protocol.SSH:
            cmd_env, tmp_id = _ssh_auth_env(github_cfg=github_cfg)
            url = urljoin(github_cfg.ssh_url(), github_repo_path)
        elif protocol is Protocol.HTTPS:
            url = url_with_credentials(github_cfg, github_repo_path)
        else:
            raise NotImplementedError

        args = ['--quiet']
        if checkout_branch is not None:
            args += ['--branch', checkout_branch, '--single-branch']
        args += [url, target_directory]

        repo = git.Git()
        if protocol is Protocol.SSH:
            with repo.custom_environment(**cmd_env):
                repo.clone(*args)
        else:
            repo.clone(*args)

        if protocol is Protocol.SSH:
            os.unlink(tmp_id.name)

        return GitHelper(
            repo=target_directory,
            github_cfg=github_cfg,
            github_repo_path=github_repo_path,
        )

    def _changed_file_paths(self):
        lines = git.cmd.Git(self.repo.working_tree_dir).status('--porcelain=1', '-z').split('\x00')
        # output of git status --porcelain=1 and -z is guaranteed to not change in the future
        return [line[3:] for line in lines if line]

    @contextlib.contextmanager
    def _authenticated_remote(self):
        protocol = self.github_cfg.preferred_protocol()
        credentials = self.github_cfg.credentials()
        if protocol is Protocol.SSH:
            url = urljoin(self.github_cfg.ssh_url(), self.github_repo_path)
            cmd_env, tmp_id = _ssh_auth_env(github_cfg=self.github_cfg)
        elif protocol is Protocol.HTTPS:
            url = url_with_credentials(
                github_cfg=self.github_cfg,
                github_repo_path=self.github_repo_path,
                technical_user_name=credentials.username(),
            )
            cmd_env = os.environ
        else:
            raise NotImplementedError

        cmd_env["GIT_AUTHOR_NAME"] = credentials.username()
        cmd_env["GIT_AUTHOR_EMAIL"] = credentials.email_address()
        cmd_env['GIT_COMMITTER_NAME'] = credentials.username()
        cmd_env['GIT_COMMITTER_EMAIL'] = credentials.email_address()

        remote = git.remote.Remote.add(
            repo=self.repo,
            name=random_str(),
            url=url,
        )
        logger.debug(f'authenticated {remote.name=} using {protocol=}')

        try:
            yield (cmd_env, remote)
        finally:
            self.repo.delete_remote(remote)
            if protocol is Protocol.SSH:
                os.unlink(tmp_id.name)

    def submodule_update(self):
        protocol = self.github_cfg.preferred_protocol()
        if protocol is Protocol.SSH:
            cmd_env, _ = _ssh_auth_env(github_cfg=self.github_cfg)
        else:
            cmd_env = {}

        with self.repo.git.custom_environment(**cmd_env):
            # avoid GitPython's submodule implementation due to bugs and lack of maintenance as
            # recommended by maintainers:
            # https://github.com/gitpython-developers/GitPython/discussions/1536
            self.repo.git.submodule('update')

    def _actor(self):
        if self.github_cfg:
            credentials = self.github_cfg.credentials()
            return git.Actor(credentials.username(), credentials.email_address())
        return None

    def index_to_commit(self, message, parent_commits=None) -> git.Commit:
        '''moves all diffs from worktree to a new commit without modifying branches.
        The worktree remains unchanged after the method returns.

        @param parent_commits: optional iterable of parent commits; head is used if absent
        @return the git.Commit object representing the newly created commit
        '''
        if not parent_commits:
            parent_commits = [self.repo.head.commit]
        else:
            def to_commit(commit: git.Commit | str):
                if isinstance(commit, git.Commit):
                    return commit
                elif isinstance(commit, str):
                    return self.repo.commit(commit)
                else:
                    raise ValueError(commit)
            parent_commits = [to_commit(commit) for commit in parent_commits]

        # add all changes
        git.cmd.Git(self.repo.working_tree_dir).add('.')
        tree = self.repo.index.write_tree()

        if self.github_cfg:
            actor = self._actor()

            create_commit = functools.partial(
                git.Commit.create_from_tree,
                author=actor,
                committer=actor,
            )
        else:
            create_commit = git.Commit.create_from_tree

        commit = create_commit(
            repo=self.repo,
            tree=tree,
            parent_commits=parent_commits,
            message=message
        )
        self.repo.index.reset()
        return commit

    def add_and_commit(self, message):
        '''
        adds changed and new files (`git add .`) and creates a commit, potentially updating the
        current branch (`git commit`). If a github_cfg is present, author and committer are set.

        see `index_to_commit` for an alternative implementation that will leave less side-effects
        in the underlying git repository and worktree.
        '''
        self.repo.git.add(self.repo.working_tree_dir)

        actor = self._actor()
        return self.repo.index.commit(
            message=message,
            author=actor,
            committer=actor,
        )

    def push(self, from_ref, to_ref):
        with self._authenticated_remote() as (cmd_env, remote):
            with remote.repo.git.custom_environment(**cmd_env):
                results = remote.push(':'.join((from_ref, to_ref)))
                if not results:
                    return # according to remote.push's documentation, empty results indicate
                    # an error. however, the documentation seems to be wrong
                if len(results) > 1:
                    raise NotImplementedError('more than one result (do not know how to handle')

                push_info: git.remote.PushInfo = results[0]
                if push_info.flags & push_info.ERROR:
                    raise RuntimeError('git-push failed (see stderr output)')

    def rebase(self, commit_ish: str):

        credentials = self.github_cfg.credentials()
        cmd_env = os.environ.copy()
        cmd_env["GIT_AUTHOR_NAME"] = credentials.username()
        cmd_env["GIT_AUTHOR_EMAIL"] = credentials.email_address()
        cmd_env['GIT_COMMITTER_NAME'] = credentials.username()
        cmd_env['GIT_COMMITTER_EMAIL'] = credentials.email_address()

        with self.repo.git.custom_environment(**cmd_env):
            self.repo.git.rebase('--quiet', commit_ish)

    def add_note(self, body: str, commit: str):
        with self._authenticated_remote() as (cmd_env, remote):
            with remote.repo.git.custom_environment(**cmd_env):
                remote.repo.git.notes('add', '-f', '-m', body, commit.hexsha)

    def fetch_head(self, ref: str):
        with self._authenticated_remote() as (cmd_env, remote):
            with remote.repo.git.custom_environment(**cmd_env):
                fetch_result = remote.fetch(ref)[0]
                return fetch_result.commit

    def fetch_tags(self):
        with self._authenticated_remote() as (cmd_env, remote):
            with remote.repo.git.custom_environment(**cmd_env):
                remote.fetch(tags=True, recurse_submodules='no')


def url_with_credentials(
    github_cfg: GithubConfig,
    github_repo_path: str,
    technical_user_name: str | None =None
):
    base_url = urllib.parse.urlparse(github_cfg.http_url())

    if technical_user_name:
        credentials = github_cfg.credentials(technical_user_name=technical_user_name)
    else:
        credentials = github_cfg.credentials()

    # prefer auth token
    secret = credentials.auth_token() or credentials.passwd()

    credentials_str = ':'.join((credentials.username(), secret))
    url = urllib.parse.urlunparse((
        base_url.scheme,
        '@'.join((credentials_str, base_url.hostname)),
        github_repo_path,
        '',
        '',
        ''
    ))
    return url
