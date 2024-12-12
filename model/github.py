# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import collections.abc
import enum
import random
import re

from urllib.parse import urlparse

import github3.github
import github3.exceptions

from model.base import (
    BasicCredentials,
    NamedModelElement,
    ModelValidationError,
)
import ci.util
import gitutil


class Protocol(enum.StrEnum):
    SSH = 'ssh'
    HTTPS = 'https'


class GithubConfig(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''

    def purpose_labels(self):
        return set(self.raw.get('purpose_labels', ()))

    def ssh_url(self):
        if Protocol.SSH not in self.available_protocols():
            raise RuntimeError(f"SSH protocol is not available for Github config '{self.name()}'")
        return self.raw.get('sshUrl')

    def http_url(self):
        if Protocol.HTTPS not in self.available_protocols():
            raise RuntimeError(f"HTTPS protocol is not available for Github config '{self.name()}'")
        return self.raw.get('httpUrl')

    def api_url(self):
        return self.raw.get('apiUrl')

    def matches_api_url(self, api_url):
        return api_url == self.api_url()

    def tls_validation(self):
        return not self.raw.get('disable_tls_validation')

    @property
    def preferred_protocol(self):
        return self.available_protocols()[0]

    def available_protocols(self):
        '''Return available git protocols, in order of preference (most preferred protcol first)
        '''
        return [Protocol(value) for value in self.raw.get('available_protocols')]

    def credentials(self, technical_user_name: str = None):
        technical_users = self._technical_user_credentials()
        if technical_user_name:
            for credential in technical_users:
                if credential.username() == technical_user_name:
                    return credential
            raise ModelValidationError(
                f'Did not find technical user "{technical_user_name}" '
                f'for Github config "{self.name()}"'
            )

        return random.choice(technical_users)

    def credentials_with_most_remaining_quota(self):
        credentials = self._technical_user_credentials()
        if len(credentials) < 2:
            return credentials[0]

        if self.hostname() == 'github.com':
            ApiCtor = github3.github.GitHub
            api_kwargs = {}
        else:
            ApiCtor = github3.github.GitHubEnterprise
            api_kwargs = {'url': self.http_url()}

        def rate_limit_remaining(credentials) -> int:
            api = ApiCtor(token=credentials.auth_token(), **api_kwargs)
            try:
                return api.ratelimit_remaining
            except github3.exceptions.ConnectionError:
                return 0

        best_credentials = max(credentials, key=rate_limit_remaining)

        return best_credentials

    def _technical_user_credentials(self) -> collections.abc.Iterable["GithubCredentials"]:
        return [
            GithubCredentials(user) for user in self.raw.get('technical_users')
        ]

    def hostname(self):
        if not (parsed := urlparse(self.http_url())).hostname:
            return None
        return parsed.hostname.lower()

    def matches_hostname(self, host_name):
        return host_name.lower() == self.hostname()

    '''
        repos which the user is for
    '''
    def repo_urls(self) -> list[str]:
        return self.raw.get('repo_urls', ())

    def matches_repo_url(self, repo_url):
        parsed_repo_url = ci.util.urlparse(repo_url)

        if not self.repo_urls():
            return self.matches_hostname(host_name=parsed_repo_url.hostname)

        repo_url = ci.util.urljoin(parsed_repo_url.hostname, parsed_repo_url.path)
        for repo_url_regex in self.repo_urls():
            if re.fullmatch(repo_url_regex, repo_url, re.RegexFlag.IGNORECASE):
                return True

        return False

    def git_cfg(
        self,
        *,
        repo_url=None,
        repo_path=None,
    ) -> gitutil.GitCfg:
        '''
        returns a GitCfg (for creating gitutil.GitHelper) for the given repository-url.

        Exactly one of `repo_url`, `repo_path` must be passed. If repo_url does not specify a schema,
        it is derived from this cfg's preferred_protocol, and patched into repo_url for GitCfg.

        If `repo_path` is passed, it is expected to be of form `{org}/{repo_name}`, and appended to
        this cfg's hostname. preferred_protocol will be honoured.
        '''
        if not (bool(repo_url) ^ bool(repo_path)):
            raise ValueError('exactly one of repo_url, repo_path must be passed')
        if repo_url:
            # monkey-patch schema, otherwise take url as-is
            if not '://' in repo_url:
                if self.preferred_protocol is Protocol.SSH:
                    repo_url = f'ssh://git@{repo_url}'
                elif self.preferred_protocol is Protocol.HTTPS:
                    repo_url = f'https://{repo_url}'

        if repo_path:
            if self.preferred_protocol is Protocol.SSH:
                repo_url = f'ssh://git@{self.hostname()}/{repo_path.strip("/")}'
            elif self.preferred_protocol is Protocol.HTTPS:
                repo_url = f'https://{self.hostname()}/{repo_path.strip("/")}'
            else:
                raise ValueError(self.preferred_protocol)

        credentials = self.credentials_with_most_remaining_quota()

        # parse schema from URL (in case repo-url was passed w/ schema, it may differ from
        # preferred protocol)
        if (scheme := urlparse(repo_url).scheme) == 'ssh':
            auth = credentials.private_key()
            auth_type = gitutil.AuthType.SSH
        elif scheme == 'https':
            auth = (
                credentials.username(),
                credentials.auth_token(),
            )
            auth_type = gitutil.AuthType.HTTP_TOKEN
        else:
            raise ValueError(scheme, repo_url)

        return gitutil.GitCfg(
            repo_url=repo_url,
            user_name=credentials.username(),
            user_email=credentials.email_address(),
            auth=auth,
            auth_type=auth_type,
        )

    def _optional_attributes(self):
        return (
            'httpUrl',
            'purpose_labels',
            'sshUrl',
            'repo_urls',
        )

    def _required_attributes(self):
        return [
            'apiUrl',
            'available_protocols',
            'disable_tls_validation',
            'technical_users',
        ]

    def validate(self):
        super().validate()

        available_protocols = self.available_protocols()
        if len(available_protocols) < 1:
            raise ModelValidationError(
                'At least one available protocol must be configured '
                f"for Github config '{self.name()}'"
            )
        if Protocol.SSH in available_protocols and not self.ssh_url():
            raise ModelValidationError(
                f"SSH url is missing for Github config '{self.name()}'"
            )
        if Protocol.HTTPS in available_protocols and not self.http_url():
            raise ModelValidationError(
                f"HTTP url is missing for Github config '{self.name()}'"
            )

        self.credentials() and self.credentials().validate() # XXX refactor


class GithubCredentials(BasicCredentials):
    '''
    Not intended to be instantiated by users of this module
    '''

    def auth_token(self) -> str:
        tokens = self.raw.get('auth_tokens', None)
        if tokens:
            return random.choice(tokens)
        # fallback to single token
        return self.raw.get('authToken')

    def secondary_auth_token(self) -> str:
        return self.raw.get('secondary_authToken')

    def set_auth_token(self, auth_token):
        self.raw['authToken'] = auth_token

    def private_key(self):
        return self.raw.get('privateKey')

    def email_address(self):
        return self.raw.get('emailAddress')

    def _required_attributes(self):
        required_attribs = set(super()._required_attributes())
        return required_attribs | set(('authToken', 'emailAddress'))

    def _optional_attributes(self):
        return (
            'privateKey',
            'secondary_authToken',
            'recovery-codes',
        )
