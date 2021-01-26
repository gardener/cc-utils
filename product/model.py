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

import abc
import dataclasses
import functools
import typing
import urllib.parse
from enum import Enum

import dacite

import ccc.oci
from model.base import ModelBase, ModelValidationError
from ci.util import not_none, urljoin, check_type

import version as ver

#############################################################################
## product descriptor model

# the asset name component descriptors are stored as part of component github releases
COMPONENT_DESCRIPTOR_ASSET_NAME = 'component_descriptor.yaml'

dc = dataclasses.dataclass


class SchemaVersion(Enum):
    V1 = 'v1'


class Relation(Enum):
    LOCAL = 'local' # dependency is created by declaring component itself
    THIRD_PARTY = '3rdparty'


class ComponentType(Enum):
    GARDENER_COMPONENT = 'gardenerComponent'

    OCI_IMAGE = 'ociImage'
    WEB = 'web'
    GENERIC = 'generic'


class InvalidComponentReferenceError(ModelValidationError):
    pass


@dc
class Meta:
    schemaVersion: SchemaVersion = SchemaVersion.V1


class ProductModelBase(ModelBase):
    '''
    Base class for product model classes.

    Not intended to be instantiated.
    '''

    def __init__(self, **kwargs):
        raw_dict = {**kwargs}
        super().__init__(raw_dict=raw_dict)
        self.validate()


class Version:
    '''
    A Gardener Component Version. Accepts versions compliant to semver-v2 and Gardener-specific
    extends of semver-v2:
    - prefixed `v` is allowed
    - versions may omit patch-level

    Also accepts versions not compliant to semver-v2 or the afforementioned extensions with
    reduced functionality.

    Any sets of Version objects are sortable. If the underlying version is semver-v2-compliant
    (considering the above-described relaxations), sorting is done according to version arithmetics
    as defined by semver-v2.

    Otherwise, comparisons are done as defined by Python's str class. Note that this will result
    in different sorting semantics to be applied for sets containing both valid and invalid
    semver versions.
    '''
    def __init__(self, version: str):
        self._version_str = str(not_none(version))

        try:
            self._version_semver = ver.parse_to_semver(self._version_str)
        except ValueError:
            self._version_semver = None

    def is_valid_semver(self):
        return self._version_semver is not None

    def __comparables(self, other):
        check_type(other, Version) # must only compare to other Version instances

        # to find out which version representations to compare for sorting, we need to
        # handle different cases:

        if self._version_semver and other._version_semver:
            # both versions are semver versions -> use semver arithmethics
            return (self._version_semver, other._version_semver)
        elif self.is_valid_semver() and not other.is_valid_semver():
            # excactly other version is semver -> normalise (strip leading v..) and str-compare
            return (str(self._version_semver), other._version_str)
        elif not self.is_valid_semver() and other.is_valid_semver():
            # exactly our version is semver -> normalise (strip leading v..) and str-compare
            return (self._version_str, str(other._version_semver))
        else:
            # fallback to str-compare only
            return (self._version_str, other._version_str)

    def __eq__(self, other):
        if not isinstance(other, Version):
            return False

        if self._version_semver and other._version_semver:
            return self._version_semver == other._version_semver

        return self._version_str == other._version_str

    def __hash__(self):
        return hash(self._version_str)

    def __lt__(self, other):
        own_version, other_version = self.__comparables(other)
        return own_version.__lt__(other_version)

    def __le__(self, other):
        own_version, other_version = self.__comparables(other)
        return own_version.__le__(other_version)

    def __gt__(self, other):
        own_version, other_version = self.__comparables(other)
        return own_version.__gt__(other_version)

    def __ge__(self, other):
        own_version, other_version = self.__comparables(other)
        return own_version.__ge__(other_version)

    def __repr__(self):
        return f'Version("{self._version_str}")'

    def __str__(self):
        return self._version_str


class DependencyBase(ModelBase):
    '''
    Base class for dependencies

    Not intended to be instantiated.
    '''
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.validate()

    def _required_attributes(self):
        return {'name', 'version'}

    def _optional_attributes(self):
        return {'relation'}

    def name(self):
        return self.raw.get('name')

    def version(self):
        return self.raw.get('version')

    def relation(self):
        '''
        declares the relation of this dependency towards the declaring component.
        Concretely put, whether the component builds the dependency itself, or just
        references it as an external / 3rd-party dependency.

        Required for migrating towards component-descriptor v2

        defaults to THIRD_PARTY
        '''
        return Relation(self.raw.get('relation', Relation.THIRD_PARTY))

    @abc.abstractmethod
    def type_name(self):
        '''
        returns the dependency type name (component, generic, ..)
        '''
        raise NotImplementedError

    def __has_same_name(self, other):
        if not isinstance(other, DependencyBase):
            return False
        return self.name() == other.name()

    def __comparables(self, other):
        if self.__has_same_name(other):
            return (Version(self.version()), Version(other.version()))
        else:
            return (self.name(), other.name())

    def __eq__(self, other):
        if not isinstance(other, DependencyBase):
            return False
        return self.name() == other.name() and self.version() == other.version()

    def __lt__(self, other):
        own, other = self.__comparables(other)
        return own.__lt__(other)

    def __le__(self, other):
        own, other = self.__comparables(other)
        return own.__le__(other)

    def __gt__(self, other):
        own, other = self.__comparables(other)
        return own.__gt__(other)

    def __ge__(self, other):
        own, other = self.__comparables(other)
        return own.__ge__(other)

    def __hash__(self):
        return hash((self.name(), self.version()))


class ComponentDescriptor(ProductModelBase):
    @staticmethod
    def from_dict(raw_dict: dict):
        # determine scheme version
        if not (meta_dict := raw_dict.get('meta')):
            schema_version = SchemaVersion.V1
        else:
            meta = dacite.from_dict(
                data_class=Meta,
                data=meta_dict,
                config=dacite.Config(
                    cast=[SchemaVersion],
                )
            )
            schema_version = meta.schemaVersion

        if schema_version == SchemaVersion.V1:
            return ComponentDescriptorV1(**raw_dict)
        else:
            raise ModelValidationError(f'unknown schema version: {schema_version}')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'components' not in self.raw:
            self.raw['components'] = []
        if 'component_overwrites' not in self.raw:
            self.raw['component_overwrites'] = []
        if 'meta' not in self.raw:
            self.raw['meta'] = {'schemaVersion': SchemaVersion.V1.value}

    def _optional_attributes(self):
        return {'components', 'component_overwrites', 'meta'}

    def components(self):
        return (Component(raw_dict=raw_dict) for raw_dict in self.raw['components'])

    def component(self, component_reference):
        if not isinstance(component_reference, ComponentReference):
            name, version = component_reference
            component_reference = ComponentReference.create(name=name, version=version)

        return next(
            filter(lambda c: c == component_reference, self.components()),
            None
        )

    def add_component(self, component):
        self.raw['components'].append(component.raw)

    def component_overwrites(self):
        return (ComponentOverwrites(raw_dict=raw_dict)
            for raw_dict in self.raw['component_overwrites'])

    def component_overwrite(self, declaring_component):
        overwrite = next(filter(
            lambda co: co.declaring_component() == declaring_component, self.component_overwrites(),
            ),
            None,
        )
        if not overwrite:
            overwrite = ComponentOverwrites.create(declaring_component=declaring_component)
            self._add_component_overwrite(component_overwrite=overwrite)

        return overwrite

    def _add_component_overwrite(self, component_overwrite):
        self.raw['component_overwrites'].append(component_overwrite.raw)


class ComponentDescriptorV1(ComponentDescriptor):
    pass


class ComponentName(object):
    @staticmethod
    def validate_component_name(name: str):
        not_none(name)

        if len(name) == 0:
            raise InvalidComponentReferenceError('Component name must not be empty')

        # valid component names are fully qualified github repository URLs without a schema
        # (e.g. github.com/example_org/example_name)
        if urllib.parse.urlparse(name).scheme:
            raise InvalidComponentReferenceError('Component name must not contain schema')

        # prepend dummy schema so that urlparse will parse away the hostname
        parsed = urllib.parse.urlparse('dummy://' + name)

        if not parsed.hostname:
            raise InvalidComponentReferenceError(name)

        path_parts = parsed.path.strip('/').split('/')
        if not len(path_parts) == 2:
            raise InvalidComponentReferenceError(
                'Component name must end with github repository path'
            )

        return name

    @staticmethod
    def from_github_repo_url(repo_url):
        parsed = urllib.parse.urlparse(repo_url)
        if parsed.scheme:
            component_name = repo_url = urljoin(*parsed[1:3])
        else:
            component_name = repo_url

        return ComponentName(name=component_name)

    def __init__(self, name: str):
        self._name = ComponentName.validate_component_name(name)

    def name(self):
        return self._name

    def github_host(self):
        return self.name().split('/')[0]

    def github_organisation(self):
        return self.name().split('/')[1]

    def github_repo(self):
        return self.name().split('/')[2]

    def github_repo_path(self):
        return self.github_organisation() + '/' + self.github_repo()

    def config_name(self):
        return self.github_host().replace('.', '_')

    def github_url(self):
        # hard-code schema to https
        return 'https://' + self.github_host()

    def github_repo_url(self):
        # hard-code schema to https
        return 'https://' + self.name()

    def __eq__(self, other):
        if not isinstance(other, ComponentName):
            return False
        return self.name() == other.name()

    def __hash__(self):
        return hash((self.name()))


class ComponentReference(DependencyBase):
    @staticmethod
    def create(name, version):
        return ComponentReference(
            raw_dict={'name': name, 'version':version},
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._componentName = ComponentName(kwargs['raw_dict']['name'])

    def type_name(self):
        return 'component'

    def github_host(self):
        return self._componentName.github_host()

    def github_organisation(self):
        return self._componentName.github_organisation()

    def github_repo(self):
        return self._componentName.github_repo()

    def github_repo_path(self):
        return self._componentName.github_repo_path()

    def config_name(self):
        return self._componentName.config_name()

    def validate(self):
        ComponentName.validate_component_name(self.raw.get('name'))
        super().validate()

    def __eq__(self, other):
        if not isinstance(other, ComponentReference):
            return False
        return (self.name(), self.version()) == (other.name(), other.version())

    def __hash__(self):
        return hash((self.name(), self.version()))

    def __repr__(self):
        return f'ComponentReference: {self.name()}:{self.version()}'


class ContainerImageReference(DependencyBase):
    @staticmethod
    def create(name, version):
        return ContainerImageReference(
            raw_dict={'name': name, 'version': version}
        )

    def type_name(self):
        return 'container_image'

    def __repr__(self):
        return f'ContainerImage: {self.name()}|{self.image_reference()}|{self.version()}'


class ContainerImage(ContainerImageReference):
    @staticmethod
    def create(name, version, image_reference, relation:Relation=Relation.THIRD_PARTY):
        if isinstance(relation, Relation):
            relation_str = relation.value
        else:
            relation_str = str(relation)

        return ContainerImage(
            raw_dict={
                'name': name,
                'version': version,
                'image_reference': image_reference,
                'relation': relation_str,
            }
        )

    def _required_attributes(self):
        return super()._required_attributes() | {'image_reference'}

    def image_reference(self):
        return self.raw.get('image_reference')

    def image_name(self):
        return self.raw.get('name')

    @functools.lru_cache
    def image_reference_with_digest(self):
        oci_client = ccc.oci.oci_client()
        return oci_client.to_digest_hash(self.image_reference())

    def image_digest(self):
        return f'@{self.image_reference_with_digest().split("@")[1]}'

    def validate(self):
        img_ref = check_type(self.image_reference(), str)
        # XXX this is an incomplete validation that will only detect some obvious errors,
        # such as missing image tag
        if not ':' in img_ref:
            raise ModelValidationError(f'img ref does not contain tag separator (:): {img_ref}')
        name, tag = img_ref.rsplit(':', 1)
        if not name:
            raise ModelValidationError(f'img ref name must not be empty: {img_ref}')
        if not tag:
            raise ModelValidationError(f'img ref tag must not be empty: {img_ref}')


class WebDependencyReference(DependencyBase):
    @staticmethod
    def create(name, version):
        return WebDependencyReference(
            raw_dict={'name': name, 'version': version}
        )

    def type_name(self):
        return 'web'


class WebDependency(WebDependencyReference):
    @staticmethod
    def create(name, version, url):
        return WebDependency(
            raw_dict={'name':name, 'version':version, 'url':url}
        )

    def type_name(self):
        return 'web'

    def _required_attributes(self):
        return super()._required_attributes() | {'url'}

    def url(self):
        return self.raw.get('url')


class GenericDependencyReference(DependencyBase):
    @staticmethod
    def create(name, version):
        return GenericDependencyReference(raw_dict={'name':name, 'version':version})

    def type_name(self):
        return 'generic'


class GenericDependency(GenericDependencyReference):
    @staticmethod
    def create(name, version):
        return GenericDependency(raw_dict={'name':name, 'version':version})


class Component(ComponentReference):
    @staticmethod
    def create(name, version):
        return Component(raw_dict={'name':name, 'version':version})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.raw.get('dependencies'):
            self.raw['dependencies'] = {}

    def _optional_attributes(self):
        return {'dependencies'}

    def dependencies(self):
        return ComponentDependencies(raw_dict=self.raw['dependencies'])

    def add_dependencies(self, dependencies):
        '''Convenience method for adding multiple dependencies'''
        for dependency in dependencies:
            self.add_dependency(dependency)

    def add_dependency(self, dependency:DependencyBase):
        '''Convencience method for adding a single dependency. Delegates to the relevant
        ComponentDependencies method.
        '''
        if isinstance(dependency, ComponentReference):
            self.dependencies().add_component_dependency(dependency)
        elif isinstance(dependency, ContainerImage):
            self.dependencies().add_container_image_dependency(dependency)
        elif isinstance(dependency, WebDependency):
            self.dependencies().add_web_dependency(dependency)
        elif isinstance(dependency, GenericDependency):
            self.dependencies().add_generic_dependency(dependency)
        else:
            raise NotImplementedError


class ComponentDependencies(ModelBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for attrib_name in ('container_images', 'components', 'web', 'generic'):
            if attrib_name not in self.raw:
                self.raw[attrib_name] = []

    def _optional_attributes(self):
        return {'container_images', 'components', 'web', 'generic'}

    def container_images(self):
        return (ContainerImage(raw_dict=raw_dict) for raw_dict in self.raw.get('container_images'))

    def components(self):
        return (ComponentReference(raw_dict=raw_dict) for raw_dict in self.raw.get('components'))

    def web_dependencies(self):
        return (WebDependency(raw_dict=raw_dict) for raw_dict in self.raw.get('web'))

    def generic_dependencies(self):
        return (GenericDependency(raw_dict=raw_dict) for raw_dict in self.raw.get('generic'))

    def references(self, type_name: str):
        reference_ctor = reference_type(type_name).create
        if type_name == 'container_image':
            attrib = 'container_images'
        elif type_name == 'component':
            attrib = 'components'
        elif type_name == 'web':
            attrib = 'web'
        elif type_name == 'generic':
            attrib = 'generic'
        else:
            raise ValueError('unknown refererence type: ' + str(type_name))

        for ref_dict in self.raw.get(attrib):
            yield reference_ctor(name=ref_dict['name'], version=ref_dict['version'])

    def add_container_image_dependency(self, container_image):
        if container_image not in self.container_images():
            self.raw['container_images'].append(container_image.raw)

    def add_component_dependency(self, component_reference):
        if component_reference not in self.components():
            self.raw['components'].append(component_reference.raw)

    def add_web_dependency(self, web_dependency):
        if web_dependency not in self.web_dependencies():
            self.raw['web'].append(web_dependency.raw)

    def add_generic_dependency(self, generic_dependency):
        if generic_dependency not in self.generic_dependencies():
            self.raw['generic'].append(generic_dependency.raw)


class ComponentOverwrites(ModelBase):
    @staticmethod
    def create(declaring_component):
        return ComponentOverwrites(
            raw_dict={
                'declaring_component': {
                    'name': declaring_component.name(),
                    'version': declaring_component.version(),
                }
            }
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not 'dependency_overwrites' in self.raw:
            self.raw['dependency_overwrites'] = []

    def _required_attributes(self):
        return {'declaring_component'}

    def declaring_component(self) -> ComponentReference:
        declaring_comp = self.raw['declaring_component']
        return ComponentReference.create(
            name=declaring_comp['name'],
            version=declaring_comp['version']
        )

    def dependency_overwrites(self) -> typing.Iterable['DependencyOverwrites']:
        return (DependencyOverwrites(raw_dict) for raw_dict in self.raw['dependency_overwrites'])

    def dependency_overwrite(self, referenced_component, create_if_absent=False):
        overwrites = next(
            filter(
                lambda do: do.references() == referenced_component,
                self.dependency_overwrites()
            ),
            None
        )
        if not overwrites and create_if_absent:
            overwrites = DependencyOverwrites.create(referenced_component=referenced_component)
            self.raw['dependency_overwrites'].append(overwrites.raw)
        return overwrites

    def _add_dependency_overwrite(self, dependency_overwrite):
        self.raw['dependency_overwrites'].append(dependency_overwrite.raw)

    def __eq__(self, other):
        if not isinstance(other, ComponentOverwrites):
            return False
        return self.raw == other.raw


class DependencyOverwrites(ModelBase):
    @staticmethod
    def create(referenced_component):
        return DependencyOverwrites(
            raw_dict={
                'references': {
                    'name': referenced_component.name(),
                    'version': referenced_component.version(),
                }
            }
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not 'container_images' in self.raw:
            self.raw['container_images'] = []

    def _required_attributes(self):
        return {'references'}

    def references(self) -> ComponentReference:
        return ComponentReference.create(**self.raw['references'])

    def container_images(self):
        return (ContainerImage(raw_dict=raw_dict) for raw_dict in self.raw.get('container_images'))

    def container_image(self, name: str, version: str):
        image = next(
            filter(
                lambda img: img.name() == name and img.version() == version,
                self.container_images(),
            ),
            None
        )
        return image

    def add_container_image_overwrite(self, container_image: ContainerImage):
        if not container_image in self.container_images():
            self.raw['container_images'].append(container_image.raw)

    def __eq__(self, other):
        if not isinstance(other, DependencyOverwrites):
            return False
        return self.raw == other.raw


def reference_type(name: str):
    check_type(name, str)
    if name == 'component':
        return ComponentReference
    if name == 'container_image':
        return ContainerImageReference
    if name == 'generic':
        return GenericDependencyReference
    if name == 'web':
        return WebDependencyReference
    raise ValueError('unknown dependency type name: ' + str(name))
