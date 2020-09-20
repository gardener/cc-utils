# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

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
