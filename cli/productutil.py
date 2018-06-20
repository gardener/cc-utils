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
from concurrent.futures import ThreadPoolExecutor
import yaml
import json

from github.util import GitHubHelper, _create_github_api_object
from util import CliHints, parse_yaml_file, ctx, info, fail
import product.model
from product.model import Product, Component, ComponentReference, ContainerImage
from product.util import merge_products
from product.scanning import ProtecodeUtil
import protecode.client

def upload_product_images(
    protecode_cfg_name: str,
    product_cfg_file: CliHints.existing_file(),
    protecode_group_id: int=5,
    parallel_jobs: int=4,
    cve_threshold: int=7,
    ):
    cfg_factory = ctx().cfg_factory()
    protecode_cfg = cfg_factory.protecode(protecode_cfg_name)
    protecode_api = protecode.client.from_cfg(protecode_cfg)
    protecode_util = ProtecodeUtil(protecode_api=protecode_api, group_id=protecode_group_id)

    product_model = Product.from_dict(
        raw_dict=parse_yaml_file(product_cfg_file)
    )

    executor = ThreadPoolExecutor(max_workers=parallel_jobs)
    tasks = _create_tasks(product_model, protecode_util)
    results = executor.map(lambda task: task(), tasks)

    for result in results:
        info('result: {r}'.format(r=result))
        analysis_result = result.result

        vulnerable_components = list(filter(
            lambda c: c.highest_major_cve_severity() >= cve_threshold, analysis_result.components()
        ))

        if vulnerable_components:
            highest_cve = max(map(lambda c: c.highest_major_cve_severity(), vulnerable_components))
            if highest_cve >= cve_threshold:
                info('Highest found CVE Severity: {cve} - Action required'.format(cve=highest_cve))
        else:
            info('CVE below configured threshold - clean')


def _create_task(protecode_util, container_image, component, wait_for_result):
    def task_function():
        return protecode_util.upload_image(
            container_image=container_image,
            component=component,
            wait_for_result=True,
        )
    return task_function


def _create_tasks(product_model, protecode_util):
    for component in product_model.components():
        info('processing component: {c}:{v}'.format(c=component.name(), v=component.version()))
        component_dependencies = component.dependencies()
        for container_image in component_dependencies.container_images():
            info('processing container image: {c}:{cir}'.format(
                c=component.name(),
                cir=container_image.image_reference(),
                )
            )
            yield _create_task(
                    protecode_util=protecode_util,
                    container_image=container_image,
                    component=component,
                    wait_for_result=True,
                    )


def component_descriptor(
    name: str,
    version: str,
    component_dependencies: [str],
    container_image_dependencies: [str],
):
    component = Component.create(name=name, version=version)
    dependencies = component.dependencies()

    for component_dependency_str in component_dependencies:
        cname, cversion = component_dependency_str.split(':')
        component_ref = ComponentReference.create(name=cname, version=cversion)
        dependencies.add_component_dependency(component_ref)

    for container_image_dependency in container_image_dependencies:
        ci_dependency = ContainerImage.create(image_reference=container_image_dependency)
        dependencies.add_container_image_dependency(ci_dependency)

    product_dict = {'components': [component.raw]}
    print(yaml.dump(product_dict, indent=2))


def merge_descriptors(descriptors: [str]):
    if len(descriptors) < 2:
        fail('at least two descriptors are required for merging')

    parse_product_file = lambda f: Product.from_dict(parse_yaml_file(f))
    merged = parse_product_file(descriptors[0])

    for descriptor in map(parse_product_file, descriptors[1:]):
        merged = merge_products(merged, descriptor)

    # workaround snd-issues (TODO: remove snd)
    cleansed_dict = json.loads(json.dumps(merged.raw))

    print(yaml.dump(cleansed_dict, indent=2))


def retrieve_component_descriptor(
    name: str,
    version: str,
    github_org: str='gardener',
    github_cfg_name: str='github_com',
):
    cfg_factory = ctx().cfg_factory()
    github_cfg = cfg_factory.github(github_cfg_name)

    github_helper = GitHubHelper(
        github=_create_github_api_object(github_cfg),
        repository_owner=github_org,
        repository_name=name
    )

    print(github_helper.retrieve_asset_contents(
        release_tag=version,
        asset_label=product.model.COMPONENT_DESCRIPTOR_ASSET_NAME
        )
    )

