# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
# under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

import util
from product.util import (
    ComponentResolver,
    ComponentDescriptorResolver,
    diff_components,
)


def component_diff_since_last_release(
    component_name,
    component_version,
    component_descriptor,
    cfg_factory,
):
    component = util.not_none(component_descriptor.component((component_name, component_version)))

    resolver = ComponentResolver(cfg_factory=cfg_factory)
    last_release_version = resolver.greatest_release_before(
        component_name=component_name,
        version=component_version
    )

    if not last_release_version:
        util.warning('could not determine last release version')
        return None
    last_release_version = str(last_release_version)
    util.info('last released version: ' + str(last_release_version))

    descriptor_resolver = ComponentDescriptorResolver(cfg_factory=cfg_factory)
    last_released_component_descriptor = descriptor_resolver.retrieve_descriptor(
            (component_name, last_release_version)
    )
    last_released_component = last_released_component_descriptor.component(
        (component_name, last_release_version)
    )

    diff = diff_components(
        left_components=component.dependencies().components(),
        right_components=last_released_component.dependencies().components(),
    )
    return diff
