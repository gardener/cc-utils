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

from enum import Enum

from model.base import ModelBase, NamedModelElement
from protecode.model import AnalysisResult
from util import parse_yaml_file, not_none

#############################################################################
## product descriptor model

class Product(NamedModelElement):
    @staticmethod
    def from_dict(name: str, raw_dict: dict):
        return Product(name=name, raw_dict=raw_dict)

    def components(self):
        return map(Component, self.snd.components)


class ComponentReference(ModelBase):
    def name(self):
        return self.snd.name

    def version(self):
        return self.snd.version


class Component(ComponentReference):
    def dependencies(self):
        return ComponentDependencies(raw_dict=self.snd.dependencies)


class ComponentDependencies(ModelBase):
    def container_images(self):
        if not self.snd.container_images:
            return ()
        return map(ContainerImage, self.snd.container_images)

    def components(self):
        if not self.snd.components:
            return ()
        return map(ComponentReference, self.snd.components)


class ContainerImage(ModelBase):
    def image_reference(self):
        return self.snd.image_reference


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

