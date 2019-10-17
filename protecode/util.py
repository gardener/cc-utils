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

import deprecated
import requests.exceptions

import ccc.protecode
import container.registry
import product.util
from product.scanning import ContainerImageGroup, ProtecodeUtil, ProcessingMode
from ci.util import info, warning, verbose, error, success, urljoin
from product.model import (
    ComponentDescriptor,
    UploadResult,
)
from protecode.model import (
    License,
    highest_major_cve_severity,
)


def upload_grouped_images(
    protecode_cfg,
    component_descriptor,
    protecode_group_id=5,
    parallel_jobs=8,
    cve_threshold=7,
    ignore_if_triaged=True,
    processing_mode=ProcessingMode.UPLOAD_IF_CHANGED,
    image_reference_filter=(lambda component, container_image: True),
    reference_group_ids=(),
):
    executor = ThreadPoolExecutor(max_workers=parallel_jobs)
    protecode_api = ccc.protecode.client(protecode_cfg)
    protecode_api.set_maximum_concurrent_connections(parallel_jobs)
    protecode_util = ProtecodeUtil(
        protecode_api=protecode_api,
        processing_mode=processing_mode,
        group_id=protecode_group_id,
        reference_group_ids=reference_group_ids,
    )

    def _upload_task(component, image_group):
        image_group = ContainerImageGroup(
            component=component,
            container_images=image_group,
        )

        def _task():
            yield from protecode_util.upload_container_image_group(
                container_image_group=image_group,
            )

        return _task

    def _upload_tasks():
        for component in component_descriptor.components():
            for image_group in product.util._grouped_effective_images(
                component_descriptor=component_descriptor,
                component=component
            ):
                image_group = [
                    image for image in image_group
                    if image_reference_filter(component, image)
                ]
                if image_group:
                    yield _upload_task(component=component, image_group=image_group)

    tasks = _upload_tasks()
    results = tuple(executor.map(lambda task: task(), tasks))

    def flatten_results():
        for result_set in results:
            yield from result_set

    results = list(flatten_results())

    relevant_results = filter_and_display_upload_results(
        upload_results=results,
        cve_threshold=cve_threshold,
        ignore_if_triaged=ignore_if_triaged,
    )

    _license_report = license_report(upload_results=results)

    return (relevant_results, _license_report)


@deprecated.deprecated
def upload_images(
    protecode_cfg,
    product_descriptor,
    protecode_group_id=5,
    parallel_jobs=8,
    cve_threshold=7,
    ignore_if_triaged=True,
    processing_mode=ProcessingMode.UPLOAD_IF_CHANGED,
    image_reference_filter=(lambda component, container_image: True),
    reference_group_ids=(),
) -> typing.Iterable[typing.Tuple[UploadResult, int]]:
    executor = ThreadPoolExecutor(max_workers=parallel_jobs)
    protecode_api = ccc.protecode.client(protecode_cfg)
    protecode_api.set_maximum_concurrent_connections(parallel_jobs)
    protecode_util = ProtecodeUtil(
        protecode_api=protecode_api,
        processing_mode=processing_mode,
        group_id=protecode_group_id,
        reference_group_ids=reference_group_ids,
    )
    tasks = _create_tasks(
        product_descriptor,
        protecode_util,
        image_reference_filter
    )
    results = tuple(executor.map(lambda task: task(), tasks))

    relevant_results = filter_and_display_upload_results(
        upload_results=results,
        cve_threshold=cve_threshold,
        ignore_if_triaged=ignore_if_triaged,
    )

    _license_report = license_report(upload_results=results)

    return (relevant_results, _license_report)


def download_images(
    component_descriptor: ComponentDescriptor,
    upload_registry_prefix: str,
    image_reference_filter=(lambda component, container_image: True),
    parallel_jobs=8, # eight is a good number
):
    '''
    downloads all matching container images, discarding the retrieved contents afterwards.
    While this may seem pointless, this actually does server a purpose. Namely, we use the
    vulnerability scanning service offered by GCR. However, said scanning service will only
    continue to run (and thus update vulnerability reports) for images that keep being
    retrieved occasionally (relevant timeout being roughly 4w).
    '''
    image_refs = [
        container_image.image_reference()
        for component, container_image
        in product.util._enumerate_effective_images(
            component_descriptor=component_descriptor,
        )
        if image_reference_filter(component, container_image)
    ]

    # XXX deduplicate this again (copied from product/scanning.py)
    def upload_image_ref(image_reference):
        image_name, tag = image_reference.rsplit(':', 1)
        mangled_reference = ':'.join((
            image_name.replace('.', '_'),
            tag
        ))
        return urljoin(upload_registry_prefix, mangled_reference)

    image_refs = [upload_image_ref(ref) for ref in image_refs]

    info(f'downloading {len(image_refs)} container images to simulate consumption')

    executor = ThreadPoolExecutor(max_workers=parallel_jobs)

    def retrieve_image(image_reference: str):
        try:
            container.registry.retrieve_container_image(image_reference=image_reference)
            info(f'downloaded {image_reference}')
        except Exception:
            warning(f'failed to retrieve {image_reference}')
            import traceback
            traceback.print_exc()

    # force generator to be exhausted
    tuple(executor.map(retrieve_image, image_refs))
    success(f'successfully retrieved {len(image_refs)} container images')


def license_report(
    upload_results: typing.Sequence[UploadResult],
) -> typing.Sequence[typing.Tuple[UploadResult, typing.Set[License]]]:
    for upload_result in upload_results:
        if isinstance(upload_result, UploadResult):
            analysis_result = upload_result.result
        else:
            analysis_result = upload_result

        licenses = {
            component.license() for component in analysis_result.components()
            if component.license()
        }
        yield (upload_result, licenses)


def filter_and_display_upload_results(
    upload_results: typing.Sequence[UploadResult],
    cve_threshold=7,
    ignore_if_triaged=True,
) -> typing.Iterable[typing.Tuple[UploadResult, int]]:
    # we only require the analysis_results for now

    results_without_components = []
    results_below_cve_thresh = []
    results_above_cve_thresh = []

    for upload_result in upload_results:
        if isinstance(upload_result, UploadResult):
            result = upload_result.result
        else:
            result = upload_result

        components = result.components()
        if not components:
            results_without_components.append(upload_result)
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
            results_above_cve_thresh.append((upload_result, greatest_cve))
            continue
        else:
            results_below_cve_thresh.append((upload_result, greatest_cve))
            continue

    if results_without_components:
        warning(f'Protecode did not identify components for {len(results_without_components)}:\n')
        for result in results_without_components:
            print(result.result.display_name())
        print('')

    def render_results_table(upload_results: typing.Sequence[typing.Tuple[UploadResult, int]]):
        header = ('Component Name', 'Greatest CVE')
        results = sorted(upload_results, key=lambda e: e[1])

        def to_result(result):
            if isinstance(result, UploadResult):
                return result.result
            return result

        result = tabulate.tabulate(
            map(lambda r: (to_result(r[0]).display_name(), r[1]), results),
            headers=header,
            tablefmt='fancy_grid',
        )
        print(result)

    if results_below_cve_thresh:
        info(f'The following components were below configured cve threshold {cve_threshold}')
        render_results_table(upload_results=results_below_cve_thresh)
        print('')

    if results_above_cve_thresh:
        warning('The following components have critical vulnerabilities:')
        render_results_table(upload_results=results_above_cve_thresh)

    return results_above_cve_thresh


def _create_task(protecode_util, container_image, component):
    def task_function():
        try:
            upload_result = protecode_util.upload_image(
                container_image=container_image,
                component=component,
            )
            return upload_result
        except requests.exceptions.ConnectionError:
            error(
                'A connection error occurred. This might be due problems with Protecode. '
                'Please try executing the image scan job again.'
                )
            sys.exit(1)
    return task_function


def _create_tasks(product_model, protecode_util, image_reference_filter):
    for component, container_image in product.util._enumerate_effective_images(
        component_descriptor=product_model,
    ):
        if image_reference_filter(component, container_image):
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
