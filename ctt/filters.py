# SPDX-FileCopyrightText: 2021 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import abc
import reutil

import gci.componentmodel as cm


class FilterBase:
    @abc.abstractmethod
    def matches(
        self,
        component: cm.Component,
        resource: cm.Resource,
    ):
        pass


class MatchAllFilter(FilterBase):
    '''
    A filter matching everything. Use at the end of a processing.cfg
    '''
    def matches(
        self,
        component: cm.Component,
        resource: cm.Resource,
    ):
        return True


class ImageFilter(FilterBase):
    def __init__(
        self,
        include_image_refs=(),
        exclude_image_refs=(),
        include_image_names=(),
        exclude_image_names=(),
    ):
        self._image_ref_filter = reutil.re_filter(
            include_regexes=include_image_refs,
            exclude_regexes=exclude_image_refs,
            value_transformation=lambda oci_resource: oci_resource.access.imageReference,
        )
        self._image_name_filter = reutil.re_filter(
            include_regexes=include_image_names,
            exclude_regexes=exclude_image_names,
            value_transformation=lambda resource: resource.name,
        )

    def matches(
        self,
        component: cm.Component,
        resource: cm.Resource,
    ):
        if resource.type is not cm.ResourceType.OCI_IMAGE:
            return False

        return self._image_ref_filter(resource) and \
            self._image_name_filter(resource)


class ComponentFilter(FilterBase):
    def __init__(
        self,
        include_component_names=(),
        exclude_component_names=(),
    ):
        self._comp_name_filter = reutil.re_filter(
            include_regexes=include_component_names,
            exclude_regexes=exclude_component_names,
            value_transformation=lambda component: component.name,
        )

    def matches(
        self,
        component: cm.Component,
        resource: cm.Resource,
    ):
        return self._comp_name_filter(component)
