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

import urllib.parse
from copy import deepcopy
from enum import Enum

from model.base import ModelBase, NamedModelElement, ModelValidationError
from protecode.model import AnalysisResult
from util import parse_yaml_file, not_none

#############################################################################
## product descriptor model

# the asset name component descriptors are stored as part of component github releases
COMPONENT_DESCRIPTOR_ASSET_NAME = 'component_descriptor.yaml'


class ProductModelBase(ModelBase):
    '''
    Base class for product model classes.

    Not intended to be instantiated.
    '''
    def __init__(self, **kwargs):
        raw_dict = {**kwargs}
        super().__init__(raw_dict=raw_dict)


class DependencyBase(ModelBase):
    '''
    Base class for dependencies

    Not intended to be instantiated.
    '''
    def name(self):
        return self.raw.get('name')

    def version(self):
        return self.raw.get('version')

    def __eq__(self, other):
        if not isinstance(other, DependencyBase):
            return False
        return self.raw == other.raw

    def __hash__(self):
        return hash(tuple(sorted(self.raw.items())))


class Product(ProductModelBase):
    @staticmethod
    def from_dict(raw_dict: dict):
        return Product(**raw_dict)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not 'components' in self.raw:
            self.raw['components'] = []

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


class ComponentReference(DependencyBase):
    @staticmethod
    def validate_component_name(name: str):
        not_none(name)

        if len(name) == 0:
            raise ModelValidationError('Component name must not be empty')

        # valid component names are fully qualified github repository URLs without a schema
        # (e.g. github.com/example_org/example_name)
        if urllib.parse.urlparse(name).scheme:
            raise ModelValidationError('Component name must not contain schema')

        # prepend dummy schema so that urlparse will parse away the hostname
        parsed = urllib.parse.urlparse('dummy://' + name)

        if not parsed.hostname:
            raise ModelValidationError(name)

        path_parts = parsed.path.strip('/').split('/')
        if not len(path_parts) == 2:
            raise ModelValidationError('Component name must end with github repository path')

    @staticmethod
    def create(name, version):
        return ComponentReference(raw_dict={'name':name, 'version':version})

    def github_host(self):
        return self.name().split('/')[0]

    def github_organisation(self):
        return self.name().split('/')[1]

    def github_repo(self):
        return self.name().split('/')[2]

    def _validate_dict(self):
        ComponentReference.validate_component_name(self.raw.get('name'))

    def __eq__(self, other):
        if not isinstance(other, ComponentReference):
            return False
        return (self.name(), self.version()) == (other.name(), other.version())


class ContainerImage(DependencyBase):
    @staticmethod
    def create(name, version, image_reference):
        return ContainerImage(
            raw_dict={'name':name, 'version':version, 'image_reference':image_reference}
        )

    def image_reference(self):
        return self.raw.get('image_reference')


class WebDependency(DependencyBase):
    @staticmethod
    def create(name, version, url):
        return WebDependency(
            raw_dict={'name':name, 'version':version, 'url':url}
        )

    def url(self):
        return self.raw.get('url')


class GenericDependency(DependencyBase):
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

    def dependencies(self):
        return ComponentDependencies(raw_dict=self.raw['dependencies'])


class ComponentDependencies(ModelBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for attrib_name in ('container_images', 'components', 'web', 'generic'):
            if not attrib_name in self.raw:
                self.raw[attrib_name] = []

    def container_images(self):
        return (ContainerImage(raw_dict=raw_dict) for raw_dict in self.raw.get('container_images'))

    def components(self):
        return (ComponentReference(raw_dict=raw_dict) for raw_dict in self.raw.get('components'))

    def web_dependencies(self):
        return (WebDependency(raw_dict=raw_dict) for raw_dict in self.raw.get('web'))

    def generic_dependencies(self):
        return (GenericDependency(raw_dict=raw_dict) for raw_dict in self.raw.get('generic'))

    def add_container_image_dependency(self, container_image):
        if not container_image in self.container_images():
            self.raw['container_images'].append(container_image.raw)

    def add_component_dependency(self, component_reference):
        if not component_reference in self.components():
            self.raw['components'].append(component_reference.raw)

    def add_web_dependency(self, web_dependency):
        if not web_dependency in self.web_dependencies():
            self.raw['web'].append(web_dependency.raw)

    def add_generic_dependency(self, generic_dependency):
        if not generic_dependency in self.generic_dependencies():
            self.raw['generic'].append(generic_dependency.raw)


#############################################################################
## upload result model

class UploadStatus(Enum):
    SKIPPED_ALREADY_EXISTED = 1
    UPLOADED_PENDING = 2
    UPLOADED_DONE = 4

class UploadResult(object):
    def __init__(
            self,
            status: UploadStatus,
            component: Component,
            container_image: ContainerImage,
            result: AnalysisResult,
    ):
        self.status = not_none(status)
        self.component = not_none(component)
        self.container_image = not_none(container_image)
        if result:
            self.result = result
        else:
            self.result = None

    def __str__(self):
        return '{c}:{ir} - {s}'.format(
            c=self.component.name(),
            ir=self.container_image.image_reference(),
            s=self.status
        )

