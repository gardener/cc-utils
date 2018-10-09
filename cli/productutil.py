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
import argparse
import github3.exceptions
import yaml
import json

from util import CliHints, CliHint, parse_yaml_file, ctx, fail
from product.model import (
    Component,
    ComponentReference,
    ContainerImage,
    GenericDependency,
    Product,
    WebDependency,
)
from product.util import merge_products, ComponentDescriptorResolver
from protecode.util import (
    upload_images,
    ProcessingMode
)


def upload_product_images(
    protecode_cfg_name: str,
    product_cfg_file: CliHints.existing_file(),
    processing_mode: CliHint(
        choices=list(ProcessingMode),
        type=ProcessingMode,
    )=ProcessingMode.UPLOAD_IF_CHANGED,
    protecode_group_id: int=5,
    parallel_jobs: int=4,
    cve_threshold: int=7,
):
    cfg_factory = ctx().cfg_factory()
    protecode_cfg = cfg_factory.protecode(protecode_cfg_name)

    product_descriptor = Product.from_dict(
        raw_dict=parse_yaml_file(product_cfg_file)
    )

    upload_images(
        protecode_cfg=protecode_cfg,
        product_descriptor=product_descriptor,
        protecode_group_id=protecode_group_id,
        parallel_jobs=parallel_jobs,
        cve_threshold=cve_threshold,
        processing_mode=processing_mode,
    )


def _parse_dependency_str_func(
        factory_function,
        required_attributes=('name', 'version'),
        forbid_extra_attribs=True
    ):
    def parse_dependency_str(token):
        try:
            parsed = json.loads(token)
        except json.decoder.JSONDecodeError as jde:
            raise argparse.ArgumentTypeError('Invalid JSON document: ' + '\n'.join(jde.args))
        missing_attribs = [attrib for attrib in required_attributes if attrib not in parsed]
        if missing_attribs:
            raise argparse.ArgumentTypeError('missing required attributes: {ma}'.format(
                ma=', '.join(missing_attribs))
            )
        if forbid_extra_attribs:
            extra_attribs = [
                    attrib for attrib in parsed.keys() if attrib not in required_attributes
            ]
            if extra_attribs:
                raise argparse.ArgumentTypeError('unknown attributes: {ua}'.format(
                    ua=', '.join(extra_attribs))
                )
        return factory_function(**parsed)
    return parse_dependency_str


_parse_component_deps = _parse_dependency_str_func(
    factory_function=ComponentReference.create
)
_parse_container_image_deps = _parse_dependency_str_func(
    factory_function=ContainerImage.create,
    required_attributes=('name', 'version', 'image_reference')
)
_parse_web_deps = _parse_dependency_str_func(
    factory_function=WebDependency.create,
    required_attributes=('name', 'version', 'url')
)
_parse_generic_deps = _parse_dependency_str_func(
    factory_function=GenericDependency.create,
)


def component_descriptor(
    name: str,
    version: str,
    component_dependencies: CliHint(typehint=_parse_component_deps, action='append')=[],
    container_image_dependencies: CliHint(typehint=_parse_container_image_deps, action='append')=[],
    web_dependencies: CliHint(typehint=_parse_web_deps, action='append')=[],
    generic_dependencies: CliHint(typehint=_parse_generic_deps, action='append')=[],
):
    component = Component.create(name=name, version=version)
    component_deps = component.dependencies()

    for component_ref in component_dependencies:
        component_deps.add_component_dependency(component_ref)
    for image_dep in container_image_dependencies:
        component_deps.add_container_image_dependency(image_dep)
    for web_dep in web_dependencies:
        component_deps.add_web_dependency(web_dep)
    for generic_dep in generic_dependencies:
        component_deps.add_generic_dependency(generic_dep)

    product_dict = {'components': [component.raw]}
    print(yaml.dump(product_dict, indent=2))


def merge_descriptors(descriptors: [str]):
    if len(descriptors) < 2:
        fail('at least two descriptors are required for merging')

    def parse_product_file(f):
        return Product.from_dict(parse_yaml_file(f))

    merged = parse_product_file(descriptors[0])

    for descriptor in map(parse_product_file, descriptors[1:]):
        merged = merge_products(merged, descriptor)

    # workaround snd-issues (TODO: remove snd)
    cleansed_dict = json.loads(json.dumps(merged.raw))

    print(yaml.dump(cleansed_dict, indent=2))


def add_dependencies(
    descriptor_src_file: CliHints.existing_file(),
    component_name: str,
    component_version: str,
    descriptor_out_file: str=None,
    component_dependencies: CliHint(typehint=_parse_component_deps, action='append')=[],
    container_image_dependencies: CliHint(typehint=_parse_container_image_deps, action='append')=[],
    web_dependencies: CliHint(typehint=_parse_web_deps, action='append')=[],
    generic_dependencies: CliHint(typehint=_parse_generic_deps, action='append')=[],
):
    product = Product.from_dict(parse_yaml_file(descriptor_src_file))

    component = product.component(
        ComponentReference.create(name=component_name, version=component_version)
    )
    if not component:
        fail('component {c}:{v} was not found in {f}'.format(
            c=component_name,
            v=component_version,
            f=descriptor_src_file
        )
        )

    component_deps = component.dependencies()

    for component_ref in component_dependencies:
        component_deps.add_component_dependency(component_ref)
    for image_dep in container_image_dependencies:
        component_deps.add_container_image_dependency(image_dep)
    for web_dep in web_dependencies:
        component_deps.add_web_dependency(web_dep)
    for generic_dep in generic_dependencies:
        component_deps.add_generic_dependency(generic_dep)

    product_dict = json.loads(json.dumps({'components': [component.raw]}))
    if not descriptor_out_file:
        print(yaml.dump(product_dict, indent=2))
    else:
        with open(descriptor_out_file, 'w') as f:
            yaml.dump(product_dict, f, indent=2)


def retrieve_component_descriptor(
    name: str,
    version: str,
):
    cfg_factory = ctx().cfg_factory()

    resolver = ComponentDescriptorResolver(
        cfg_factory=cfg_factory,
    )

    component_reference = ComponentReference.create(name=name, version=version)
    try:
        resolved_descriptor = resolver.retrieve_raw_descriptor(component_reference)
    except github3.exceptions.NotFoundError:
        fail('no component descriptor found: {n}:{v}'.format(n=name, v=version))

    print(resolved_descriptor)


def resolve_component_descriptor(
    component_descriptor_file: CliHints.existing_file(),
):
    cfg_factory = ctx().cfg_factory()

    resolver = ComponentDescriptorResolver(
        cfg_factory=cfg_factory,
    )

    with open(component_descriptor_file) as f:
        component_descriptor = Product.from_dict(yaml.load(f))

    resolved_descriptor = resolver.resolve_component_references(product=component_descriptor)

    print(yaml.dump(resolved_descriptor.raw))
