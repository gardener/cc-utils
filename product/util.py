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

from copy import deepcopy
import yaml

from github.util import GitHubHelper
from util import not_none
from .model import Product, COMPONENT_DESCRIPTOR_ASSET_NAME

class ComponentDescriptorResolver(object):
    def __init__(
        self,
        github_cfg,
        github_organisation='gardener',
    ):
        self.github_organisation = github_organisation
        self.github_cfg = github_cfg

    def _repository_helper(self, component_reference):
        return GitHubHelper(
            github_cfg=self.github_cfg,
            repository_owner=self.github_organisation,
            repository_name=component_reference.name(),
        )

    def retrieve_component_descriptor(self, component_reference, as_dict=False):
        repo_helper = self._repository_helper(component_reference)
        dependency_descriptor = repo_helper.retrieve_asset_contents(
                release_tag=component_reference.version(),
                asset_label=COMPONENT_DESCRIPTOR_ASSET_NAME,
            )
        if as_dict:
            return yaml.load(dependency_descriptor)
        else:
            return dependency_descriptor

    def resolve(self, component_reference):
        dependency_descriptor = self.retrieve_component_descriptor(
            component_reference=component_reference,
            as_dict=True,
        )
        return Product.from_dict(dependency_descriptor)


def merge_products(left_product, right_product):
    not_none(left_product)
    not_none(right_product)

    # start with a copy of left_product
    merged = Product.from_dict(raw_dict=deepcopy(dict(left_product.raw.items())))
    for component in right_product.components():
        existing_component = merged.component(component)
        if existing_component:
            # it is acceptable to add an existing component iff it is identical
            if existing_component.raw == component.raw:
                continue # skip
            else:
                raise ValueError(
                    'conflicting component definitions: {c1}, {c2}'.format(
                        c1=':'.join((existing_component.name(), existing_component.version())),
                        c2=':'.join((component.name(), component.version())),
                    )
                )
        merged.add_component(component)

    return merged


def resolve_component_references(
    product,
    component_descriptor_resolver,
):
    def unresolved_references(component):
        component_references = component.dependencies().components()
        yield from filter(lambda cr: not product.component(cr), component_references)

    merged = Product.from_dict(raw_dict=deepcopy(dict(product.raw.items())))

    for component_reference in map(unresolved_references, product.components()):
        resolved_descriptor = component_descriptor_resolver.resolve(component_reference)
        merged = merge_products(merged, resolved_descriptor)

    return merged


