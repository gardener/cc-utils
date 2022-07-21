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
import enum
import typing

import gci.componentmodel

import reutil
import version


class ComponentVersionFilterType(enum.Enum):
    INCLUDE = enum.auto()
    EXCLUDE = enum.auto()


def _ensure_resource_is_oci(resource):
    if resource.type is not gci.componentmodel.ResourceType.OCI_IMAGE:
        raise NotImplementedError
    if resource.access.type is not gci.componentmodel.AccessType.OCI_REGISTRY:
        raise NotImplementedError


def image_reference_filter(include_regexes=(), exclude_regexes=()):
    if not include_regexes and not exclude_regexes:
        return lambda container_image: True

    def to_image_reference(resource: gci.componentmodel.Resource):
        _ensure_resource_is_oci(resource)
        return resource.access.imageReference

    return reutil.re_filter(
        include_regexes=include_regexes,
        exclude_regexes=exclude_regexes,
        value_transformation=to_image_reference,
    )


def image_name_filter(include_regexes=(), exclude_regexes=()):
    if not include_regexes and not exclude_regexes:
        return lambda container_image: True

    def to_logical_name(resource: gci.componentmodel.Resource):
        _ensure_resource_is_oci(resource)
        return resource.name

    return reutil.re_filter(
        include_regexes=include_regexes,
        exclude_regexes=exclude_regexes,
        value_transformation=to_logical_name,
    )


def component_name_filter(include_regexes=(), exclude_regexes=()):
    if not include_regexes and not exclude_regexes:
        return lambda component: True

    def to_component_name(component: gci.componentmodel.Component):
        return component.name

    return reutil.re_filter(
        include_regexes=include_regexes,
        exclude_regexes=exclude_regexes,
        value_transformation=to_component_name,
    )


def component_ref_component_name_filter(include_regexes=(), exclude_regexes=()):
    if not include_regexes and not exclude_regexes:
        return lambda component: True

    def to_component_name(component: gci.componentmodel.ComponentReference):
        return component.componentName

    return reutil.re_filter(
        include_regexes=include_regexes,
        exclude_regexes=exclude_regexes,
        value_transformation=to_component_name,
    )


def _component_version_filter(
    component_name: str,
    filter_type: ComponentVersionFilterType,
    component_versions: typing.Iterable[str]=(),
):
    # Creates a filter function for a single component name and a set of component versions
    versions = [version.parse_to_semver(v) for v in component_versions]

    def to_component_name(component: gci.componentmodel.Component):
        return component.name

    name_filter_func = reutil.re_filter(
        include_regexes=[component_name],
        value_transformation=to_component_name,
    )

    def version_filter_func(component: gci.componentmodel.ComponentReference):
        if not versions:
            # if this is an exclusion filter, nothing can be excluded. Otherwise,
            # we defined the absence of a version config as "do not filter"
            return True

        in_versions = version.parse_to_semver(component.version) in versions

        if filter_type is ComponentVersionFilterType.INCLUDE:
            return in_versions

        elif filter_type is ComponentVersionFilterType.EXCLUDE:
            return not in_versions

        else:
            raise NotImplementedError(filter_type)

    def filter_func(component: gci.componentmodel.ComponentReference):
        if name_filter_func(component):
            return version_filter_func(component)
        else:
            # only care about the component we're responsible for
            return True

    return filter_func


def component_version_filter(
    component_version_filter_config: typing.Collection[typing.Dict],
    filter_type: ComponentVersionFilterType,
):
    filters = [
        _component_version_filter(
            component_name=entry['component_name'],
            component_versions=entry['component_versions'],
            filter_type=filter_type,
        )
        for entry in component_version_filter_config
    ]
    return lambda component: all(f(component) for f in filters)


def create_composite_filter_function(
  include_image_references,
  exclude_image_references,
  include_image_names,
  exclude_image_names,
  include_component_names,
  exclude_component_names,
  include_component_versions=[],
  exclude_component_versions=[],
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
    component_version_exclusion_filter = component_version_filter(
        exclude_component_versions,
        ComponentVersionFilterType.EXCLUDE,
    )
    component_version_inclusion_filter = component_version_filter(
        include_component_versions,
        ComponentVersionFilterType.INCLUDE,
    )

    def filter_function(
        component: gci.componentmodel.Component,
        resource: gci.componentmodel.Resource,
    ):
        return (
            image_reference_filter_function(resource)
            and image_name_filter_function(resource)
            and component_name_filter_function(component)
            and component_version_exclusion_filter(component)
            and component_version_inclusion_filter(component)
        )

    return filter_function
