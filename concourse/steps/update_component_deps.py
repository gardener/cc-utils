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

import os

import ci.util
import product.model


def current_product_descriptor():
    component_descriptor = os.path.join(
        ci.util.check_env('COMPONENT_DESCRIPTOR_DIR'),
        'component_descriptor',
    )
    return product.model.ComponentDescriptor.from_dict(
        ci.util.parse_yaml_file(component_descriptor),
    )


def current_component():
    product = current_product_descriptor()
    component_name = ci.util.check_env('COMPONENT_NAME')
    return _component(product, component_name=component_name)


def _component(
        product_descriptor: product.model.ComponentDescriptor,
        component_name: str,
    ):
    component = [c for c in product_descriptor.components() if c.name() == component_name]
    component_count = len(component)
    try:
      print('component names:', [c.name() for c in product_descriptor.components()])
    except:
      pass
    if component_count == 1:
        return component[0]
    elif component_count < 1:
        ci.util.fail('Did not find component {cn}'.format(cn=component_name))
    elif component_count > 1:
        ci.util.fail('Found more than one component with name ' + component_name)
    else:
        raise NotImplementedError # this line should never be reached


def upstream_reference_component(component_resolver, component_descriptor_resolver):
    component_name = ci.util.check_env('UPSTREAM_COMPONENT_NAME')
    latest_version = component_resolver.latest_component_version(component_name)

    component_reference = product.model.ComponentReference.create(
        name=component_name,
        version=latest_version,
    )

    reference_product = component_descriptor_resolver.retrieve_descriptor(
        component_reference=component_reference,
    )

    reference_component = _component(
        product_descriptor=reference_product,
        component_name=component_name,
    )

    return reference_component


def close_obsolete_pull_requests(upgrade_pull_requests, reference_component):
    open_pull_requests = [
        pr for pr in upgrade_pull_requests
        if pr.pull_request.state == 'open'
    ]
    obsolete_upgrade_requests = [
        pr for pr in open_pull_requests
        if pr.is_obsolete(reference_component=reference_component)
    ]

    for obsolete_request in obsolete_upgrade_requests:
        obsolete_request.purge()


def upgrade_pr_exists(reference, upgrade_requests):
    return any(
        [upgrade_rq.target_matches(reference=reference) for upgrade_rq in upgrade_requests]
    )
