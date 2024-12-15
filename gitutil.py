# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import contextlib
import dataclasses
import enum
import functools
import logging
import os
import tempfile
import urllib.parse

import git
import git.objects.util
import git.remote

from ci.util import random_str

logger = logging.getLogger(__name__)


class AuthType(enum.StrEnum):
    '''
    SSH: credentials for use via SSH (typically a RSA-key w/ no explicit username)
    HTTP_TOKEN: API-Token as understood by GitHub
    PRESET: assume existing .git/config contains needed cfg
    '''
    SSH = 'ssh'
    HTTP_TOKEN = 'http-token'
    PRESET = 'preset'


@dataclasses.dataclass(kw_only=True)
class GitCfg:
    '''
    Configuration for interacting w/ a git-repository. This class intentionally leaves it to caller
    whether or not to specify any values. If no values are set, this will tell `GitHelper` to
    assume underlying git-repository's `.git/config` (or effective git-config) was already
    adequately prepared. If values _are_ passed, they will be used transiently (i.e. .git/config
    will not be altered) (with the notable exception of usage in context of clone_into).

    For obvious reasons, GitHelper.clone_into will fail if repo_url is not present.

    It is left to the user to ensure repo_url matches the auth_type (e.g. if auth_type is SSH,
    repo_url *must* have ssh-scheme)

    repo_url: if set, use as remote. Note: auth_type needs to match url-schema
    user_name: if set, use as user.name
    user_email: if set user as user.email
    auth: if set, use for interactions w/ remote
    auth_type: type of auth
    '''
    repo_url: str | None = None
    user_name: str | None = None
    user_email: str | None = None
    auth: str | None = None
    auth_type: AuthType = AuthType.PRESET


def _ssh_auth_env(git_cfg: GitCfg):
    credentials = git_cfg.auth
    logger.info(f'using github-credentials with {git_cfg.user_name=}')

    tmp_id = tempfile.NamedTemporaryFile(mode='w', delete=False) # noqa; callers must unlink
    tmp_id.write(credentials)
    tmp_id.flush()

    os.chmod(tmp_id.name, 0o400)
    suppress_hostcheck = '-o "StrictHostKeyChecking no"'
    id_only = '-o "IdentitiesOnly yes"'
    cmd_env = os.environ.copy()
    cmd_env['GIT_SSH_COMMAND'] = f'ssh -v -i {tmp_id.name} {suppress_hostcheck} {id_only}'
    return (cmd_env, tmp_id)


class GitHelper:
    def __init__(
        self,
        repo,
        git_cfg: GitCfg,
    ):
        if repo is None:
            raise ValueError(repo)
        if isinstance(repo, str):
            repo = git.Repo(repo)
        if not isinstance(repo, git.Repo):
            raise ValueError(repo)

        self.repo = repo
        self.git_cfg = git_cfg

    @staticmethod
    def clone_into(
        target_directory: str,
        git_cfg: GitCfg,
        checkout_branch: str = None,
    ) -> 'GitHelper':
        if not git_cfg.repo_url:
            raise ValueError('repo-url must not be None')

        auth_type = git_cfg.auth_type
        if auth_type is AuthType.SSH:
            cmd_env, tmp_id = _ssh_auth_env(git_cfg=git_cfg)
            url = git_cfg.repo_url
        elif auth_type is AuthType.HTTP_TOKEN:
            url = _url_with_credentials(git_cfg)
        elif auth_type is AuthType.PRESET:
            url = git_cfg.repo_url
        else:
            raise NotImplementedError

        args = ['--quiet']
        if checkout_branch is not None:
            args += ['--branch', checkout_branch, '--single-branch']
        args += [url, target_directory]

        repo = git.Git()
        if auth_type is AuthType.SSH:
            with repo.custom_environment(**cmd_env):
                repo.clone(*args)
        else:
            repo.clone(*args)

        if auth_type is AuthType.SSH:
            os.unlink(tmp_id.name)

        return GitHelper(
            repo=git.Repo(target_directory),
            git_cfg=git_cfg,
        )

    def _changed_file_paths(self):
        lines = git.cmd.Git(self.repo.working_tree_dir).status('--porcelain=1', '-z').split('\x00')
        # output of git status --porcelain=1 and -z is guaranteed to not change in the future
        return [line[3:] for line in lines if line]

    @contextlib.contextmanager
    def _authenticated_remote(self):
        auth_type = self.git_cfg.auth_type

        cmd_env = os.environ.copy()
        if auth_type is AuthType.SSH:
            url = self.git_cfg.repo_url
            cmd_env, tmp_id = _ssh_auth_env(git_cfg=self.git_cfg)
        elif auth_type is AuthType.HTTP_TOKEN:
            url = _url_with_credentials(
                git_cfg=self.git_cfg,
            )
        elif auth_type is AuthType.PRESET:
            yield os.environ, self.repo.remotes[0]
            return
        else:
            raise NotImplementedError

        if (user := self.git_cfg.user_name):
            cmd_env['GIT_AUTHOR_NAME'] = user
            cmd_env['GIT_COMMITTER_NAME'] = user
        if (email := self.git_cfg.user_email):
            cmd_env['GIT_AUTHOR_EMAIL'] = email
            cmd_env['GIT_COMMITTER_EMAIL'] = email

        remote = git.remote.Remote.add(
            repo=self.repo,
            name=random_str(),
            url=url,
        )
        logger.debug(f'authenticated {remote.name=} using {auth_type=}')

        try:
            yield (cmd_env, remote)
        finally:
            self.repo.delete_remote(remote)
            if auth_type is AuthType.SSH:
                os.unlink(tmp_id.name)

    def submodule_update(self):
        auth_type = self.git_cfg.auth_type
        if auth_type is AuthType.SSH:
            cmd_env, _ = _ssh_auth_env(git_cfg=self.git_cfg)
        else:
            cmd_env = {}

        with self.repo.git.custom_environment(**cmd_env):
            # avoid GitPython's submodule implementation due to bugs and lack of maintenance as
            # recommended by maintainers:
            # https://github.com/gitpython-developers/GitPython/discussions/1536
            self.repo.git.submodule('update')

    def _actor(self):
        if not (git_cfg := self.git_cfg):
            return None

        if (user := git_cfg.user_name) and (email := git_cfg.user_email):
            return git.Actor(user, email)

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

        if (actor := self._actor()):
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
        current branch (`git commit`). If a git_cfg is present, author and committer are set.

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
        git_cfg = self.git_cfg
        cmd_env = os.environ.copy()
        if (username := git_cfg.user_name):
            cmd_env["GIT_AUTHOR_NAME"] = username
            cmd_env['GIT_COMMITTER_NAME'] = username
        if (email := git_cfg.user_email):
            cmd_env["GIT_AUTHOR_EMAIL"] = email
            cmd_env['GIT_COMMITTER_EMAIL'] = email

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


def _url_with_credentials(
    git_cfg: GitCfg,
):
    if git_cfg.auth_type is AuthType.PRESET:
        return git_cfg.repo_url
    elif git_cfg.auth_type is AuthType.SSH:
        raise ValueError('auth-url cannot be created for auth-type SSH')
    elif git_cfg.auth_type is AuthType.HTTP_TOKEN:
        pass # ok to proceed
    else:
        raise ValueError(f'not implemented: {git_cfg.auth_type=}')

    base_url = urllib.parse.urlparse(git_cfg.repo_url)
    scheme = base_url.scheme
    if not scheme:
        if git_cfg.auth_type is AuthType.SSH:
            scheme = 'ssh'
        elif git_cfg.auth_type is AuthType.HTTP_TOKEN:
            scheme = 'https'
        else:
            logger.warning(f'{git_cfg.repo_url=} does not contain a scheme')

    user, secret = git_cfg.auth
    credentials_str = f'{user}:{secret}'

    url = f'{scheme}://{credentials_str}@{base_url.netloc}{base_url.path}'
    return url
