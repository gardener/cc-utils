# Copyright (c) 2019-2020 SAP SE or an SAP affiliate company. All rights reserved. This file is
# licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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
import typing

import dacite

import gci.componentmodel as cm

from ci.util import not_none
from concourse.client.model import (
    ResourceType,
)
from concourse.model.base import (
    AttributeSpec,
    ModelBase,
    ModelValidationError,
)
import model.github


def sane_env_var_name(name):
    not_none(name)
    return name.replace('-', '_').upper()


class ResourceIdentifier:
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
        return 'ResourceId: type {t}, base_name {bn}, qualifier {q}, resource_name {rn}'.format(
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

    @classmethod
    def _attribute_specs(cls):
        return ()

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


class ResourceRegistry:
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

    def __len__(self):
        return len(self.resources_dict)

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


REPO_ATTRS = (
    AttributeSpec.optional(
        name='name',
        default='source',
        doc='''
        the logical repository name. affects environment variable names and is used to reference
        from traits.
        ''',
    ),
    AttributeSpec.optional(
        name='cfg_name',
        default=None,
        doc='''
        the github_cfg to use for authentication. Defaults to concourse-specific default
        ''',
    ),
    AttributeSpec.optional(
        name='force_push',
        default=False,
        doc='whether or not force-pushes ought to be done',
        type=bool,
    ),
    AttributeSpec.optional(
        name='trigger_paths',
        default={
            'include': [],
            'exclude': [],
        },
        doc='repository paths to either ignore or to restrict triggering to',
        type=dict,
    ),
    AttributeSpec.optional(
        name='trigger',
        default=None,
        doc='overwrites the defaults for triggering behaviour',
        type=bool,
    ),
    AttributeSpec.optional(
        name='disable_ci_skip',
        default=False,
        doc='whether to disable the ignoring of commits with [ci skip] in commit msg',
        type=bool,
    ),
    AttributeSpec.optional(
        name='branch',
        default=None,
        doc='only for non-main repository: specify branch to work with',
    ),
    AttributeSpec.optional(
        name='path',
        default=None,
        doc='github repository path (e.g. gardener/gardener)',
    ),
    AttributeSpec.optional(
        name='hostname',
        default=None,
        doc='do not use',
    ),
    AttributeSpec.optional(
        name='preferred_protocol',
        default=None,
        doc='optionally overwrites the preferred protocol to use',
        type=model.github.Protocol,
    ),
    AttributeSpec.optional(
        name='source_labels',
        default=[],
        type=typing.List[cm.Label],
        doc='labels to add to the corresponding source declaration in base-component-descriptor'
    )
)


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
            type_name = ResourceType.PULL_REQUEST.value
        else:
            type_name = ResourceType.GIT.value

        base_name = kwargs['raw_dict']['path'].replace('/', '.')

        # hack: use branch name as qualifier to support referencing the same repo
        #       multiple times (if branch differs)
        if not qualifier:
            qualifier = kwargs['raw_dict'].get('branch')

        resource_identifier = ResourceIdentifier(
            type_name=type_name,
            base_name=base_name,
            qualifier=qualifier,
            logical_name=logical_name
        )

        super().__init__(resource_identifier=resource_identifier, *args, **kwargs)

    @classmethod
    def _attribute_specs(cls):
        return REPO_ATTRS

    def custom_init(self, raw_dict):
        if raw_dict.get('trigger') is not None:
            self._trigger = raw_dict['trigger']
        else:
            self._trigger = self._is_main_repo

        self._disable_ci_skip = raw_dict.get('disable_ci_skip', False)
        if 'disable_ci_skip' not in raw_dict:
            self._disable_ci_skip = not self._is_main_repo

    def cfg_name(self):
        return self.raw['cfg_name']

    def resource_name(self):
        # TODO: replace usages with access to resource_id
        return self._resource_identifier.name() + '.' + self.branch()

    def name(self):
        # TODO: replace usages with access to resource_id
        return self._resource_identifier.name()

    def logical_name(self):
        # TODO: replace usages with access to resource_id
        return self._resource_identifier.logical_name()

    def repo_path(self):
        return self.raw['path'] # owner/repo_name

    def repo_name(self):
        return self.repo_path().split('/')[-1]

    def repo_owner(self):
        return self.repo_path().split('/')[0]

    def repo_hostname(self):
        hostname = self.raw.get('hostname')
        if hostname is not None:
            return hostname.lower()
        return None

    def branch(self):
        return self.raw['branch']

    def should_trigger(self):
        return self._trigger

    def force_push(self):
        return self.raw['force_push']

    def _trigger_paths(self):
        return self.raw['trigger_paths']

    def trigger_include_paths(self):
        paths = self._trigger_paths()
        return paths['include']

    def trigger_exclude_paths(self):
        paths = self._trigger_paths()
        return paths['exclude']

    def source_labels(self):
        return [
            dacite.from_dict(
                data_class=cm.Label,
                data=label_dict,
            ) for label_dict
            in self.source_label_dicts()
        ]

    def source_label_dicts(self):
        return self.raw['source_labels']

    def is_main_repo(self):
        return self._is_main_repo

    def disable_ci_skip(self):
        return self._disable_ci_skip

    def preferred_protocol(self):
        protocol_str = self.raw.get('preferred_protocol')
        if protocol_str:
            return model.github.Protocol(protocol_str)
        else:
            return None

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
        env_var_dict = {
            self.path_env_var_name(): self.resource_name(),
            sane_env_var_name(name) + '_BRANCH': self.branch(),
            sane_env_var_name(name) + '_GITHUB_REPO_OWNER_AND_NAME': self.repo_path(),
        }
        if self.is_main_repo():
            env_var_dict['MAIN_REPO_DIR'] = self.resource_name()
        return env_var_dict

    def path_env_var_name(self):
        return sane_env_var_name(self.logical_name() + '_PATH')

    def __str__(self):
        return 'RepositoryConfig ({cfg}:{rp}:{b})'.format(
            cfg=self.cfg_name() if self.cfg_name() else '<default>',
            rp=self.repo_path(),
            b=self.branch()
        )

    def validate(self):
        super().validate()

        try:
            for label in self.source_labels():
                pass # source_labels converts into cm.Label
        except dacite.DaciteError as e:
            raise ModelValidationError(f'Invalid {label=}') from e # pylint: disable=E0601
