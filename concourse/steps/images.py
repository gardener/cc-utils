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

import reutil

import product.model


def image_reference_filter(include_regexes=(), exclude_regexes=()):
    if not include_regexes and not exclude_regexes:
        return lambda container_image: True

    def to_image_reference(container_image: product.model.ContainerImage):
        return container_image.image_reference()

    return reutil.re_filter(
        include_regexes=include_regexes,
        exclude_regexes=exclude_regexes,
        value_transformation=to_image_reference,
    )


def image_name_filter(include_regexes=(), exclude_regexes=()):
    if not include_regexes and not exclude_regexes:
        return lambda container_image: True

    def to_logical_name(container_image: product.model.ContainerImage):
        return container_image.name()

    return reutil.re_filter(
        include_regexes=include_regexes,
        exclude_regexes=exclude_regexes,
        value_transformation=to_logical_name,
    )


def component_name_filter(include_regexes=(), exclude_regexes=()):
    if not include_regexes and not exclude_regexes:
        return lambda component: True

    def to_component_name(component):
        return component.name()

    return reutil.re_filter(
        include_regexes=include_regexes,
        exclude_regexes=exclude_regexes,
        value_transformation=to_component_name,
    )


def create_composite_filter_function(
  include_image_references,
  exclude_image_references,
  include_image_names,
  exclude_image_names,
  include_component_names,
  exclude_component_names,
):
    image_reference_filter_function = image_reference_filter(
        include_image_references,
        exclude_image_references,
    )
    image_name_filter_function = image_name_filter(
        include_image_names,
        exclude_image_names,
    )
    component_name_filter_function = component_name_filter(
        include_component_names,
        exclude_component_names,
    )

    def filter_function(component, container_image):
        return (
            image_reference_filter_function(container_image)
            and image_name_filter_function(container_image)
            and component_name_filter_function(component)
        )

    return filter_function
