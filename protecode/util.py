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

from concurrent.futures import ThreadPoolExecutor
import sys
import tabulate
import typing

import requests.exceptions

import protecode.client
from product.scanning import ProtecodeUtil, ProcessingMode
from util import info, warning, verbose, error
from product.model import (
    UploadResult,
)
from protecode.model import (
    AnalysisResult,
    highest_major_cve_severity,
)


def upload_images(
    protecode_cfg,
    product_descriptor,
    protecode_group_id=5,
    parallel_jobs=8,
    cve_threshold=7,
    ignore_if_triaged=True,
    processing_mode=ProcessingMode.UPLOAD_IF_CHANGED,
    image_reference_filter=lambda _: True,
    upload_registry_prefix: str=None,
    reference_group_ids=(),
) -> typing.Sequence[typing.Tuple[AnalysisResult, int]]:
    executor = ThreadPoolExecutor(max_workers=parallel_jobs)
    protecode_api = protecode.client.from_cfg(protecode_cfg)
    protecode_api.set_maximum_concurrent_connections(parallel_jobs)
    protecode_util = ProtecodeUtil(
        protecode_api=protecode_api,
        processing_mode=processing_mode,
        group_id=protecode_group_id,
        upload_registry_prefix=upload_registry_prefix,
        reference_group_ids=reference_group_ids,
    )
    tasks = _create_tasks(
        product_descriptor,
        protecode_util,
        image_reference_filter
    )
    results = executor.map(lambda task: task(), tasks)

    relevant_results = filter_and_display_upload_results(
        upload_results=results,
        cve_threshold=cve_threshold,
        ignore_if_triaged=ignore_if_triaged,
    )
    return relevant_results


def filter_and_display_upload_results(
    upload_results: typing.Sequence[UploadResult],
    cve_threshold=7,
    ignore_if_triaged=True,
) -> typing.Sequence[typing.Tuple[AnalysisResult, int]]:
    # we only require the analysis_results for now
    results = [r.result for r in upload_results]

    results_without_components = []
    results_below_cve_thresh = []
    results_above_cve_thresh = []

    for result in results:
        components = result.components()
        if not components:
            results_without_components.append()
            continue

        greatest_cve = -1

        for component in components:
            vulnerabilities = filter(lambda v: not v.historical(), component.vulnerabilities())
            if ignore_if_triaged:
                vulnerabilities = filter(lambda v: not v.has_triage(), vulnerabilities)
            greatest_cve_candidate = highest_major_cve_severity(vulnerabilities)
            if greatest_cve_candidate > greatest_cve:
                greatest_cve = greatest_cve_candidate

        if greatest_cve >= cve_threshold:
            results_above_cve_thresh.append((result, greatest_cve))
            continue
        else:
            results_below_cve_thresh.append((result, greatest_cve))
            continue

    if results_without_components:
        warning(f'Protecode did not identify components for {len(results_without_components)}:\n')
        for result in results_without_components:
            print(result.display_name())
        print('')

    def render_results_table(results: typing.Sequence[typing.Tuple[AnalysisResult, int]]):
        header = ('Component Name', 'Greatest CVE')
        results = sorted(results, key=lambda e: e[1])

        result = tabulate.tabulate(
            map(lambda r: (r[0].display_name(), r[1]), results),
            headers=header,
            tablefmt='fancy_grid',
        )
        print(result)

    if results_below_cve_thresh:
        info(f'The following components were below configured cve threshold {cve_threshold}')
        render_results_table(results=results_below_cve_thresh)
        print('')

    if results_above_cve_thresh:
        warning('The following components have critical vulnerabilities:')
        render_results_table(results=results_above_cve_thresh)

    return results_above_cve_thresh


def _create_task(protecode_util, container_image, component):
    def task_function():
        try:
            return protecode_util.upload_image(
                container_image=container_image,
                component=component,
            )
        except requests.exceptions.ConnectionError:
            error(
                'A connection error occurred. This might be due problems with Protecode. '
                'Please try executing the image scan job again.'
                )
            sys.exit(1)
    return task_function


def _create_tasks(product_model, protecode_util, image_reference_filter):
    for component in product_model.components():
        verbose('processing component: {c}:{v}'.format(c=component.name(), v=component.version()))
        component_dependencies = component.dependencies()
        for container_image in filter(
                image_reference_filter,
                component_dependencies.container_images()
        ):
            verbose('processing container image: {c}:{cir}'.format(
                c=component.name(),
                cir=container_image.image_reference(),
            )
            )
            yield _create_task(
                    protecode_util=protecode_util,
                    container_image=container_image,
                    component=component,
            )
