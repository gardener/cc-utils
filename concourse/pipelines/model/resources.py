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

from concourse.pipelines.modelbase import ModelBase, ensure_not_none as not_none

def sane_env_var_name(name):
  return name.replace('-', '_').upper()

class ResourceIdentifier(object):
    def __init__(
        self,
        type_name,
        base_name,
        qualifier=None,
        logical_name=None,
    ):
        self._type_name = not_none(type_name)
        self._base_name = not_none(base_name)
        self._qualifier = qualifier if qualifier else ''
        self._logical_name = logical_name

    def name(self):
        parts = [self._type_name, self._base_name]
        if len(self._qualifier) > 0:
            parts.append(self._qualifier)

        return '-'.join(parts)

    def base_name(self):
        return self._base_name

    def qualifier(self):
        return self._qualifier

    def type_name(self):
        return self._type_name

    def logical_name(self):
        return self._logical_name

    def __eq__(self, other):
        if not isinstance(other, ResourceIdentifier):
            return False
        return self.name() == other.name()

    def __hash__(self):
        return hash((self._type_name, self._base_name, self._qualifier))

    def __str__(self):
        return 'ResourceId: type {t}, base_name bn}, qualifier {q}, resource_name {rn}'.format(
            t=self.type_name(),
            bn=self.base_name(),
            q=self._qualifier,
            rn=self.name()
        )


class Resource(ModelBase):
    def __init__(
        self,
        resource_identifier: ResourceIdentifier,
        *args,
        **kwargs
    ):
        self._resource_identifier = resource_identifier
        super().__init__(*args, **kwargs)

    def resource_identifier(self):
        return self._resource_identifier

    def __str__(self):
        return 'Resource with id: {id}'.format(id=self._resource_identifier)

    def __eq__(self, other):
        if not isinstance(other, Resource):
            return False
        return self._resource_identifier == other._resource_identifier

    def __hash__(self):
        return self._resource_identifier.__hash__()


class ResourceRegistry(object):
    def __init__(self):
        self.resources_dict = {}

    def __contains__(self, item):
        if isinstance(item, Resource):
            id = item.resource_identifier()
        elif isinstance(item, ResourceIdentifier):
            id = item
        else:
            return False
        return id in self.resources_dict

    def __getitem__(self, item):
        if isinstance(item, Resource):
            id = item.resource_identifier()
        elif isinstance(item, ResourceIdentifier):
            id = item
        else:
            id = item
        return self.resources_dict[id]

    def add_resource(self, resource, discard_duplicates=True):
        if not isinstance(resource, Resource):
            raise ValueError('not an instance of Resource')

        resource_id = resource.resource_identifier()
        if resource_id in self.resources_dict:
            if discard_duplicates:
                return # nothing to do (resource already existed)
            raise ValueError('insertion conflict: {id}'.format(id=resource_id))
        self.resources_dict[resource_id] = resource

    def resources(self, type_name, qualifier=None):
        def filter_expr(resource):
            id = resource.resource_identifier()
            if not id.type_name() == type_name:
                return False
            if qualifier is not None and id.qualifier() != qualifier:
                return False
            return True

        return filter(filter_expr, self.resources_dict.values())

    def resource(self, resource_identifier):
        return self[resource_identifier]


class RepositoryConfig(Resource):
    def __init__(
            self,
            logical_name: str=None,
            qualifier: str=None,
            is_pull_request: bool=False,
            is_main_repo: bool=False,
            *args, **kwargs
        ):
        self._is_pull_request = is_pull_request
        self._is_main_repo = is_main_repo


        # todo: handle "qualifier"
        if is_pull_request:
            type_name='pull-request'
        else:
            type_name='git'

        base_name = kwargs['raw_dict']['path'].replace('/', '_')

        resource_identifier = ResourceIdentifier(
            type_name=type_name,
            base_name=base_name,
            qualifier=qualifier,
            logical_name=logical_name
        )

        super().__init__(resource_identifier=resource_identifier, *args, **kwargs)

    def custom_init(self, raw_dict):
        if 'trigger' in raw_dict:
            self._trigger = raw_dict['trigger']
        else:
            self._trigger = self._is_main_repo

    def cfg_name(self):
        return self.raw.get('cfg_name', None)

    def resource_name(self):
        # TODO: replace usages with access to resource_id
        return self._resource_identifier.name()

    def name(self):
        # TODO: replace usages with access to resource_id
        return self._resource_identifier.name()

    def logical_name(self):
        # TODO: replace usages with access to resource_id
        return self._resource_identifier.logical_name()

    def git_resource_name(self):
        # todo: either rm this method, or resource_name
        return self.resource_name()

    def repo_path(self):
        return self.raw['path'] # owner/repo_name

    def repo_name(self):
        return self.repo_path().split('/')[-1]

    def repo_owner(self):
        return self.repo_path().split('/')[0]

    def repo_hostname(self):
        return self.raw.get('hostname').lower()

    def branch(self):
        return self.raw['branch']

    def should_trigger(self):
        return self._trigger

    def is_main_repo(self):
        return self._is_main_repo

    def head_sha_path(self):
        if self._is_pull_request:
            head_sha = '.git/head_sha'
        else:
            head_sha = '.git/HEAD'
        return os.path.join(self.resource_name(), head_sha)

    def pr_id_path(self):
        if not self._is_pull_request:
            raise RuntimeError('resource is not a pull-request')
        return os.path.join(self.resource_name(), '.git', 'id')

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

