# Copyright (c) 2019 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
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
import enum
import itertools
import github3.exceptions
import json
import os
import yaml

from typing import Iterable

import ccc.protecode
import container.registry
from ci.util import CliHints, CliHint, parse_yaml_file, ctx, fail, info
from product.model import (
    Component,
    ComponentReference,
    ContainerImage,
    DependencyBase,
    GenericDependency,
    ComponentDescriptor,
    WebDependency,
)
from product.util import (
    _enumerate_effective_images,
    merge_products,
    ComponentDescriptorResolver,
)
from protecode.util import (
    upload_grouped_images,
    ProcessingMode
)
import product.xml


class ValidationPolicy(enum.Enum):
    NOT_EMPTY = "not_empty"
    FORBID_EXTRA_ATTRIBUTES = "forbid_extra_attributes"

    def __str__(self):
        return self.value


def transport_triages(
    protecode_cfg_name: str,
    from_product_id: int,
    to_group_id: int,
    to_product_ids: [int],
):
    cfg_factory = ctx().cfg_factory()
    protecode_cfg = cfg_factory.protecode(protecode_cfg_name)
    api = ccc.protecode.client(protecode_cfg)

    scan_result_from = api.scan_result(product_id=from_product_id)
    scan_results_to = {
        product_id: api.scan_result(product_id=product_id)
        for product_id in to_product_ids
    }

    def target_component_versions(product_id: int, component_name: str):
        scan_result = scan_results_to[product_id]
        component_versions = {
            c.version() for c
            in scan_result.components()
            if c.name() == component_name
        }
        return component_versions

    def enum_triages():
        for component in scan_result_from.components():
            for vulnerability in component.vulnerabilities():
                for triage in vulnerability.triages():
                    yield component, triage

    triages = list(enum_triages())
    info(f'found {len(triages)} triage(s) to import')

    for to_product_id, component_name_and_triage in itertools.product(to_product_ids, triages):
        component, triage = component_name_and_triage
        for target_component_version in target_component_versions(
            product_id=to_product_id,
            component_name=component.name(),
        ):
            info(f'adding triage for {triage.component_name()}:{target_component_version}')
            api.add_triage(
                triage=triage,
                product_id=to_product_id,
                group_id=to_group_id,
                component_version=target_component_version,
            )
        info(f'added triage for {triage.component_name()} to {to_product_id}')


def upload_grouped_product_images(
    protecode_cfg_name: str,
    product_cfg_file: CliHints.existing_file(),
    processing_mode: CliHint(
        choices=list(ProcessingMode),
        type=ProcessingMode,
    )=ProcessingMode.RESCAN,
    protecode_group_id: int=5,
    parallel_jobs: int=4,
    cve_threshold: int=7,
    ignore_if_triaged: bool=True,
    reference_group_ids: [int]=[],
):
    cfg_factory = ctx().cfg_factory()
    protecode_cfg = cfg_factory.protecode(protecode_cfg_name)

    component_descriptor = ComponentDescriptor.from_dict(
        raw_dict=parse_yaml_file(product_cfg_file)
    )

    upload_results, license_report = upload_grouped_images(
        protecode_cfg=protecode_cfg,
        component_descriptor=component_descriptor,
        protecode_group_id=protecode_group_id,
        parallel_jobs=parallel_jobs,
        cve_threshold=cve_threshold,
        ignore_if_triaged=ignore_if_triaged,
        processing_mode=processing_mode,
        reference_group_ids=reference_group_ids,
    )


def _parse_dependency_str_func(
        factory_function,
        required_attributes,
        validation_policies,
    ):
    def parse_dependency_str(token):
        try:
            parsed = json.loads(token)
        except json.decoder.JSONDecodeError as jde:
            raise argparse.ArgumentTypeError('Invalid JSON document: ' + '\n'.join(jde.args))
        missing_attribs = [attrib for attrib in required_attributes if attrib not in parsed]
        if missing_attribs:
            raise argparse.ArgumentTypeError(
                f"missing required attributes: {', '.join(missing_attribs)}",
            )
        if ValidationPolicy.FORBID_EXTRA_ATTRIBUTES in validation_policies:
            extra_attribs = [
                    attrib for attrib in parsed.keys() if attrib not in required_attributes
            ]
            if extra_attribs:
                raise argparse.ArgumentTypeError(
                    f"unknown attributes: {', '.join(extra_attribs)}",
                )
        if ValidationPolicy.NOT_EMPTY in validation_policies:
            empty_attribs = [attrib for attrib in parsed.keys() if not parsed[attrib]]
            if empty_attribs:
                raise argparse.ArgumentTypeError(
                    f"no values given for attributes: {', '.join(empty_attribs)}",
                )
        return factory_function(**parsed)
    return parse_dependency_str


def component_descriptor_to_xml(
    component_descriptor: CliHints.existing_file(),
    out_file: str,
):
    component_descriptor = ComponentDescriptor.from_dict(parse_yaml_file(component_descriptor))

    image_references = [
        container_image for _, container_image
        in _enumerate_effective_images(component_descriptor=component_descriptor)
    ]

    result_xml = product.xml.container_image_refs_to_xml(
        image_references,
    )

    result_xml.write(out_file)


def _parse_component_dependencies(
    component_dependencies,
    validation_policies,
):
    _parse_component_deps = _parse_dependency_str_func(
        factory_function=ComponentReference.create,
        required_attributes=('name', 'version'),
        validation_policies=validation_policies,
    )
    return [_parse_component_deps(token) for token in component_dependencies]


def _parse_container_image_dependencies(
    container_image_dependencies,
    validation_policies,
):
    _parse_container_image_deps = _parse_dependency_str_func(
        factory_function=ContainerImage.create,
        required_attributes=('name', 'version', 'image_reference'),
        validation_policies=validation_policies,
    )
    return [_parse_container_image_deps(token) for token in container_image_dependencies]


def _parse_web_dependencies(
    web_dependencies,
    validation_policies,
):
    _parse_web_deps = _parse_dependency_str_func(
        factory_function=WebDependency.create,
        required_attributes=('name', 'version', 'url'),
        validation_policies=validation_policies,
    )
    return [_parse_web_deps(token) for token in web_dependencies]


def _parse_generic_dependencies(
    generic_dependencies,
    validation_policies,
):
    _parse_generic_deps = _parse_dependency_str_func(
        factory_function=GenericDependency.create,
        required_attributes=('name', 'version'),
        validation_policies=validation_policies,
    )
    return [_parse_generic_deps(token) for token in generic_dependencies]


def _parse_dependencies(
    component_dependencies: [str],
    container_image_dependencies: [str],
    web_dependencies: [str],
    generic_dependencies: [str],
    validation_policies: [ValidationPolicy],
) -> Iterable[DependencyBase]:
    '''Return a generator that yields all parsed dependencies'''
    yield from _parse_component_dependencies(component_dependencies, validation_policies)

    yield from _parse_container_image_dependencies(
        container_image_dependencies,
        validation_policies,
    )

    yield from _parse_web_dependencies(web_dependencies, validation_policies)

    yield from _parse_generic_dependencies(generic_dependencies, validation_policies)


def component_descriptor(
    name: str,
    version: str,
    component_dependencies: CliHint(action='append')=[],
    container_image_dependencies: CliHint(action='append')=[],
    web_dependencies: CliHint(action='append')=[],
    generic_dependencies: CliHint(action='append')=[],
    validation_policies: CliHint(
        type=ValidationPolicy,
        typehint=[ValidationPolicy],
        choices=[policy for policy in ValidationPolicy],
    )=[],
):
    component = Component.create(name=name, version=version)
    # maintain old behaviour
    if not validation_policies:
        validation_policies=[ValidationPolicy.FORBID_EXTRA_ATTRIBUTES]

    dependencies = _parse_dependencies(
        component_dependencies=component_dependencies,
        container_image_dependencies=container_image_dependencies,
        web_dependencies=web_dependencies,
        generic_dependencies=generic_dependencies,
        validation_policies=validation_policies,
    )
    component.add_dependencies(dependencies)

    product_dict = {'components': [component.raw]}
    print(yaml.dump(product_dict, indent=2))


def merge_descriptors(descriptors: [str]):
    if len(descriptors) < 2:
        fail('at least two descriptors are required for merging')

    def parse_product_file(f):
        return ComponentDescriptor.from_dict(parse_yaml_file(f))

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
    component_dependencies: CliHint(action='append')=[],
    container_image_dependencies: CliHint(action='append')=[],
    web_dependencies: CliHint(action='append')=[],
    generic_dependencies: CliHint(action='append')=[],
    validation_policies: CliHint(
        type=ValidationPolicy,
        typehint=[ValidationPolicy],
        choices=[policy for policy in ValidationPolicy],
    )=[],
):
    product = ComponentDescriptor.from_dict(parse_yaml_file(descriptor_src_file))

    component = product.component(
        ComponentReference.create(name=component_name, version=component_version)
    )
    if not component:
        fail('component {c}:{v} was not found in {f}'.format(
            c=component_name,
            v=component_version,
            f=descriptor_src_file
        ))

    # maintain old behaviour
    if not validation_policies:
        validation_policies=[ValidationPolicy.FORBID_EXTRA_ATTRIBUTES]

    dependencies = _parse_dependencies(
        component_dependencies=component_dependencies,
        container_image_dependencies=container_image_dependencies,
        web_dependencies=web_dependencies,
        generic_dependencies=generic_dependencies,
        validation_policies=validation_policies,
    )
    component.add_dependencies(dependencies)

    product_dict = {'components': [component.raw]}
    print(yaml.dump(product_dict, indent=2))

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
    component_descriptor: CliHints.existing_file(),
):
    cfg_factory = ctx().cfg_factory()

    resolver = ComponentDescriptorResolver(
        cfg_factory=cfg_factory,
    )

    component_descriptor = ComponentDescriptor.from_dict(parse_yaml_file(component_descriptor))

    resolved_descriptor = resolver.resolve_component_references(product=component_descriptor)

    print(yaml.dump(resolved_descriptor.raw))


def download_dependencies(
    component_descriptor: CliHints.existing_file(),
    out_dir: str,
):
    if not os.path.isdir(out_dir):
        os.mkdir(out_dir)

    component_descriptor = ComponentDescriptor.from_dict(parse_yaml_file(component_descriptor))
    image_references = [
        container_image.image_reference() for _, container_image
        in _enumerate_effective_images(component_descriptor=component_descriptor)
    ]

    def mangled_outfile_name(image_reference):
        mangled_fname = image_reference.replace(':', '_').replace('/', '_')
        return os.path.join(out_dir, mangled_fname + '.tar')

    for image_ref in image_references:
        fname = mangled_outfile_name(image_ref)
        with open(fname, 'wb') as f:
            container.registry.retrieve_container_image(
                image_reference=image_ref,
                outfileobj=f,
            )
        print(fname)
