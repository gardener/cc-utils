import os

from concourse.pipelines.modelbase import ModelBase

def sane_env_var_name(name):
  return name.replace('-', '_').upper()

class RepositoryConfig(ModelBase):
    def __init__(
            self,
            name: str=None,
            logical_name: str=None,
            is_pull_request: bool=False,
            is_main_repo: bool=False,
            *args, **kwargs
        ):
        if name:
            kwargs['raw_dict']['name'] = name
        if logical_name:
            kwargs['raw_dict']['logical_name'] = logical_name
        self._is_pull_request = is_pull_request
        self._is_main_repo = is_main_repo
        super().__init__(*args, **kwargs)

    def custom_init(self, raw_dict):
        if 'trigger' in raw_dict:
            self._trigger = raw_dict['trigger']
        else:
            self._trigger = self._is_main_repo

    def cfg_name(self):
        return self.raw.get('cfg_name', None)

    def resource_name(self):
        # todo: use actual resource type
        if '-' in self.name():
            suffix = '-' + self.name().split('-')[-1]
        else:
            suffix = ''

        return self.repo_path().replace('/', '_') + suffix

    def name(self):
        return self.raw['name']

    def logical_name(self):
        if self.raw.get('logical_name', None):
            return self.raw['logical_name']
        return self.raw['name']

    def git_resource_name(self):
        # todo: either rm this method, or resource_name
        return self.resource_name()

    def repo_path(self):
        return self.raw['path'] # owner/repo_name

    def repo_name(self):
        return self.repo_path().split('/')[-1]

    def repo_owner(self):
        return self.repo_path().split('/')[0]

    def branch(self):
        return self.raw['branch']

    def should_trigger(self):
        return self._trigger

    def head_sha_path(self):
        if self._is_pull_request:
            head_sha = '.git/head_sha'
        else:
            head_sha = '.git/HEAD'
        return os.path.join(self.resource_name(), head_sha)

    def env_var_value_dict(self):
        name = self.logical_name()
        return dict([
            (self.path_env_var_name() , self.resource_name()),
            (sane_env_var_name(name) + '_BRANCH', self.branch()),
            (sane_env_var_name(name) + '_GITHUB_REPO_OWNER_AND_NAME', self.repo_path()),
      ])

    def path_env_var_name(self):
        return sane_env_var_name(self.logical_name() + '_PATH')

    def __str__(self):
        return 'RepositoryConfig ({cfg}:{rp}:{b})'.format(
            cfg=self.cfg_name() if self.cfg_name() else '<default>',
            rp=self.repo_path(),
            b=self.branch()
        )

