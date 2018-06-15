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

from util import CliHints, parse_yaml_file, ctx, info
from product.model import Product, Component, ComponentReference, ContainerImage
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
        name='gardener-product',
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

    print(yaml.dump([component.raw], indent=2))


