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

from concurrent.futures import ThreadPoolExecutor

import protecode.client
from product.scanning import ProtecodeUtil, ProcessingMode
from util import info
from protecode.model import highest_major_cve_severity


def upload_images(
    protecode_cfg,
    product_descriptor,
    protecode_group_id=5,
    parallel_jobs=8,
    cve_threshold=7,
    ignore_if_triaged=True,
    processing_mode=ProcessingMode.UPLOAD_IF_CHANGED,
):
    executor = ThreadPoolExecutor(max_workers=parallel_jobs)
    protecode_api = protecode.client.from_cfg(protecode_cfg)
    protecode_util = ProtecodeUtil(
        protecode_api=protecode_api,
        processing_mode=processing_mode,
        group_id=protecode_group_id,
    )
    tasks = _create_tasks(product_descriptor, protecode_util)
    results = executor.map(lambda task: task(), tasks)

    for result in results:
        info('result: {r}'.format(r=result))
        analysis_result = result.result

        if not analysis_result.components():
            continue

        vulnerability_scores = list()

        for component in analysis_result.components():
            vulnerabilities = filter(lambda v: not v.historical(), component.vulnerabilities())
            if ignore_if_triaged:
                vulnerabilities = filter(lambda v: not v.has_triage(), vulnerabilities)
            highest_cve = highest_major_cve_severity(vulnerabilities)
            vulnerability_scores.append(highest_cve)

        highest_cve = max(vulnerability_scores)

        if highest_cve >= cve_threshold:
            info('Highest found CVE Severity: {cve} - Action required'.format(cve=highest_cve))
        else:
            info('CVE below configured threshold - clean')


def _create_task(protecode_util, container_image, component):
    def task_function():
        return protecode_util.upload_image(
            container_image=container_image,
            component=component,
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
            )
