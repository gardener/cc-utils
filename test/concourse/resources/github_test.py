import mako
import os
import pytest

from unittest.mock import MagicMock

from concourse.model.resources import RepositoryConfig
from model.github import GithubConfig
from resources import mako_resource_dir


class TestGithubMakoResource(object):
    test_file = os.path.join(mako_resource_dir(), 'github.mako')

    @pytest.fixture()
    def cfg_set(self):
        def _cfg_set(
            gh_config_name="foo",
            ssh_url='made-up-ssh-url',
            http_url='made-up-http-url',
            api_url='made-up-api-url',
            disable_tls_validation=False,
            webhook_token='made-up-token',
            available_protocols=['https', 'ssh'],
            tu_username='made-up-name',
            tu_password='made-up-password',
            tu_private_key='first key line \n second key line \n third key line',
            tu_auth_token='made-up-token',
            tu_email_address='username@host.domain',
        ):
            cfg_set_mock = MagicMock()
            gh_cfg = GithubConfig(
                name=gh_config_name,
                raw_dict={
                    'sshUrl': ssh_url,
                    'httpUrl': http_url,
                    'apiUrl': api_url,
                    'disable_tls_validation': disable_tls_validation,
                    'webhook_token': webhook_token,
                    'available_protocols': available_protocols,
                    'technical_users': [{
                            'username': tu_username,
                            'password': tu_password,
                            'privateKey': tu_private_key,
                            'authToken': tu_auth_token,
                            'emailAddress': tu_email_address,
                    }],
                },
            )
            cfg_set_mock.github.return_value = gh_cfg
            return cfg_set_mock
        return _cfg_set

    @pytest.fixture()
    def repo_cfg(self):
        def _repo_cfg(
            path='organisation/reponame',
            branch='master',
            include_paths=['path/to/include'],
            exclude_paths=['path/to/exclude'],
        ):
            return RepositoryConfig(raw_dict={
                'path': path,
                'branch': branch,
                'trigger_paths': {
                    'include': include_paths,
                    'exclude': exclude_paths,
                }
            })
        return _repo_cfg

    @pytest.mark.parametrize('configure_webhook', [True, False])
    @pytest.mark.parametrize('require_label', [None, 'some-label'])
    @pytest.mark.parametrize('include_paths', [[], ['path/to/include']])
    @pytest.mark.parametrize('exclude_paths', [[], ['path/to/exclude']])
    @pytest.mark.parametrize('available_protocols', [['ssh','https'],['https', 'ssh']])
    def test_pr_resource_contains_required_attributes(
        self,
        repo_cfg,
        cfg_set,
        configure_webhook,
        require_label,
        include_paths,
        exclude_paths,
        available_protocols,
    ):
        examinee = mako.template.Template(filename=self.test_file)
        test_repo_cfg = repo_cfg(include_paths=include_paths, exclude_paths=exclude_paths)
        test_cfg_set = cfg_set(available_protocols=available_protocols)
        render_result = examinee.get_def('github_pr').render(
            test_repo_cfg,
            test_cfg_set,
            require_label,
            configure_webhook,
        )

        required_attributes = [
            'base', 'uri', 'api_endpoint', 'skip_ssl_verification', 'access_token', 'no_ssl_verify',
            'private_key', 'username', 'password',
        ]
        if require_label:
            required_attributes.append('label')
        if configure_webhook:
            required_attributes.append('configure_webhook')
        if include_paths:
            required_attributes.append('paths')
        if exclude_paths:
            required_attributes.append('ignore_paths')

        result_lines = render_result.splitlines()
        missing_attributes = []
        for attr in required_attributes:
            if not any([attr in line for line in result_lines]):
                missing_attributes.append(attr)
        if missing_attributes:
            pytest.fail(
                f'Required attributes are missing in render result: {",".join(missing_attributes)}'
            )

    @pytest.mark.parametrize('configure_webhook', [True, False])
    @pytest.mark.parametrize('include_paths', [[], ['path/to/include']])
    @pytest.mark.parametrize('exclude_paths', [[], ['path/to/exclude']])
    @pytest.mark.parametrize('available_protocols', [['ssh','https'],['https', 'ssh']])
    def test_git_resource_contains_required_attributes(
        self,
        repo_cfg,
        cfg_set,
        configure_webhook,
        include_paths,
        exclude_paths,
        available_protocols,
    ):
        examinee = mako.template.Template(filename=self.test_file)
        test_repo_cfg = repo_cfg(include_paths=include_paths, exclude_paths=exclude_paths)
        test_cfg_set = cfg_set(available_protocols=available_protocols)
        render_result = examinee.get_def('github_repo').render(
            test_repo_cfg,
            test_cfg_set,
            configure_webhook,
        )

        required_attributes = [
            'branch', 'uri', 'disable_ci_skip', 'skip_ssl_verification', 'no_ssl_verify',
            'private_key', 'username', 'password',
        ]
        if configure_webhook:
            required_attributes.append('configure_webhook')
        if include_paths:
            required_attributes.append('paths')
        if exclude_paths:
            required_attributes.append('ignore_paths')

        result_lines = render_result.splitlines()
        missing_attributes = []
        for attr in required_attributes:
            if not any([attr in line for line in result_lines]):
                missing_attributes.append(attr)
        if missing_attributes:
            pytest.fail(
                f'Required attributes are missing in render result: {",".join(missing_attributes)}'
            )
