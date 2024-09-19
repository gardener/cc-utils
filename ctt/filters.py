# SPDX-FileCopyrightText: 2021 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import abc
import enum
import reutil

import ocm


class FilterBase:
    @abc.abstractmethod
    def matches(
        self,
        component: ocm.Component,
        resource: ocm.Resource,
    ):
        pass


class MatchAllFilter(FilterBase):
    '''
    A filter matching everything. Use at the end of a processing.cfg
    '''
    def matches(
        self,
        component: ocm.Component,
        resource: ocm.Resource,
    ):
        return True


class ImageFilter(FilterBase):
    def __init__(
        self,
        include_image_refs=(),
        exclude_image_refs=(),
        include_image_names=(),
        exclude_image_names=(),
        include_artefact_types=(),
        exclude_artefact_types=(),
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
        self._include_artefact_types = include_artefact_types
        self._exclude_artefact_types = exclude_artefact_types

    def matches(
        self,
        component: ocm.Component,
        resource: ocm.Resource,
    ):
        if resource.access.type is not ocm.AccessType.OCI_REGISTRY:
            return False

        name_matches = self._image_ref_filter(resource) and \
            self._image_name_filter(resource)

        if not name_matches:
            return False

        if isinstance(resource.type, enum.Enum):
            type_name = resource.type.value
        elif isinstance(resource.type, str):
            type_name = resource.type
        elif resource.type is None:
            return name_matches
        else:
            raise ValueError(resource.type)

        for exclude_type_name in self._exclude_artefact_types:
            try:
                exclude_type = ocm.ArtefactType(exclude_type_name)
                if exclude_type is resource.type:
                    return False # type was explicitly excluded
            except ValueError:
                # fallback to str-comparison
                if exclude_type_name == type_name:
                    return False

        if not self._include_artefact_types:
            return name_matches # if no types are explicitly configured to be included, and
            # this line is reached (which means the type was also not explicitly excluded),
            # then match depending on name

        for include_type_name in self._include_artefact_types:
            try:
                include_type = ocm.ArtefactType(include_type_name)
                if include_type is resource.type:
                    return True
            except ValueError:
                # fallback to str-comparison
                if include_type_name == type_name:
                    return True
        else:
            # if types for inclusion are explicitly passed, and this line is reached, it means
            # there was not match
            return False


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
        component: ocm.Component,
        resource: ocm.Resource,
    ):
        return self._comp_name_filter(component)
