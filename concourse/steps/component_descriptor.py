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

import ci.util
from product.util import (
    ComponentResolver,
    ComponentDescriptorResolver,
    diff_components,
)

import yaml


def component_diff_since_last_release(
    component_name,
    component_version,
    component_descriptor,
    cfg_factory,
):
    component = ci.util.not_none(component_descriptor.component((component_name, component_version)))

    resolver = ComponentResolver(cfg_factory=cfg_factory)
    last_release_version = resolver.greatest_release_before(
        component_name=component_name,
        version=component_version
    )

    if not last_release_version:
        ci.util.warning('could not determine last release version')
        return None
    last_release_version = str(last_release_version)
    ci.util.info('last released version: ' + str(last_release_version))

    descriptor_resolver = ComponentDescriptorResolver(cfg_factory=cfg_factory)
    last_released_component_descriptor = descriptor_resolver.retrieve_descriptor(
            (component_name, last_release_version)
    )
    last_released_component = last_released_component_descriptor.component(
        (component_name, last_release_version)
    )

    if not last_released_component:
        ci.util.fail(
            f"Component '{component_name}' not found in the component "
            f"descriptor of the last release ({last_release_version})."
        )

    diff = diff_components(
        left_components=component.dependencies().components(),
        right_components=last_released_component.dependencies().components(),
    )
    return diff


def write_component_diff(component_diff, out_path):
    # let us write only a subset for now, namely component names with changed versions
    diff_dict = {
        'component_names_with_version_changes': list(component_diff.names_version_changed),
    }

    with open(out_path, 'w') as f:
        yaml.dump(diff_dict, f)
