# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import gci.componentmodel

import reutil


def _ensure_resource_is_oci(resource):
    if resource.type is not gci.componentmodel.ResourceType.OCI_IMAGE:
        raise NotImplementedError
    if resource.access.type is not gci.componentmodel.AccessType.OCI_REGISTRY:
        raise NotImplementedError


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
