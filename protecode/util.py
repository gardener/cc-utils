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

import collections
from concurrent.futures import ThreadPoolExecutor
import dataclasses
import logging
import tabulate
import typing

import dacite

import ccc.delivery
import ccc.gcp
import ccc.protecode
import ci.util
import cnudie.retrieve
import cnudie.util
import dso.model
import product.util
import product.v2

import gci.componentmodel as cm

from protecode.scanning_util import (
    ProcessingMode,
    ProtecodeUtil,
)
from protecode.model import (
    License,
    highest_major_cve_severity,
    CVSSVersion,
    UploadResult,
)

logger = logging.getLogger(__name__)


def upload_grouped_images(
    protecode_cfg,
    component_descriptor,
    protecode_group_id=5,
    parallel_jobs=8,
    cve_threshold=7,
    ignore_if_triaged=True,
    processing_mode=ProcessingMode.RESCAN,
    image_reference_filter=(lambda component, resource: True),
    reference_group_ids=(),
    cvss_version=CVSSVersion.V2,
):
    executor = ThreadPoolExecutor(max_workers=parallel_jobs)
    protecode_api = ccc.protecode.client(protecode_cfg)
    protecode_api.set_maximum_concurrent_connections(parallel_jobs)
    protecode_util = ProtecodeUtil(
        protecode_api=protecode_api,
        processing_mode=processing_mode,
        group_id=protecode_group_id,
        reference_group_ids=reference_group_ids,
        cvss_threshold=cve_threshold,
    )

    def _upload_task(component_resources):
        def _task():
            # force executor to actually iterate through generator
            return set(
                protecode_util.process_component_resources(
                    component_resources=component_resources,
                )
            )

        return _task

    def _upload_tasks():
        # group images of same name w/ different versions
        component_groups = collections.defaultdict(list)
        components = list(cnudie.retrieve.components(component=component_descriptor))

        for component in components:
            component_groups[component.name].append(component)

        def group_by_resource_name(
            components
        ) -> typing.Generator[
            list[cnudie.util.ComponentResource],
            None,
            None
        ]:
            # groups resources of components by resource name
            resource_groups = collections.defaultdict(list[cnudie.util.ComponentResource])
            for component in components:
                # TODO: Handle other resource types
                for resource in product.v2.resources(
                    component=component,
                    resource_types=[cm.ResourceType.OCI_IMAGE],
                    resource_access_types=[cm.AccessType.OCI_REGISTRY],
                ):
                    resource_groups[resource.name].append(
                        cnudie.util.ComponentResource(
                            component=component,
                            resource=resource,
                        )
                    )

            yield from resource_groups.values()

        def _filter_resources_to_scan(component: cm.Component, resource: cm.Resource):
            # check whether the trait was configured to filter out the resource
            configured_image_reference_filter_response = image_reference_filter(component, resource)
            if not configured_image_reference_filter_response:
                return False

            # check for scanning labels on resource in cd
            if (
                (label := resource.find_label(name=dso.labels.ScanLabelName.BINARY_ID.value))
                or (label := resource.find_label(name=dso.labels.ScanLabelName.BINARY_SCAN.value))
            ):
                if label.name == dso.labels.ScanLabelName.BINARY_SCAN.value:
                    logger.warning(f'deprecated {label.name=}')
                else:
                    scanning_hint = dacite.from_dict(
                        data_class=dso.labels.BinaryScanHint,
                        data=label.value,
                        config=dacite.Config(cast=[dso.labels.ScanPolicy]),
                    )
                    return scanning_hint.policy is dso.labels.ScanPolicy.SCAN
            else:
                return True

        for components in component_groups.values():
            for component_resources in group_by_resource_name(components):
                # all components in a component group share a name
                component_resources = [
                    r for r in component_resources
                    if _filter_resources_to_scan(r.component, r.resource)
                ]
                if component_resources:
                    yield _upload_task(component_resources=component_resources)

    tasks = _upload_tasks()
    results = tuple(executor.map(lambda task: task(), tasks))

    def flatten_results():
        for result_set in results:
            yield from result_set

    results = list(flatten_results())

    logger.info('Preparing results')
    relevant_results, results_below_threshold = filter_and_display_upload_results(
        upload_results=results,
        cvss_version=cvss_version,
        cve_threshold=cve_threshold,
        ignore_if_triaged=ignore_if_triaged,
    )

    if (delivery_client := ccc.delivery.default_client_if_available()):
        logger.info('uploading results to deliverydb')
        try:
            cve_data = [
                upload_result_to_cve_data(
                    upload_result=result[0],
                    greatest_cvss3_score=result[1],
                ) for result in relevant_results + results_below_threshold
            ]
            license_data = [
                upload_result_to_license_data(
                    upload_result=result[0],
                ) for result in relevant_results + results_below_threshold
            ]
            for data in cve_data + license_data:
                delivery_client.upload_metadata(data=data)
        except:
            import traceback
            traceback.print_exc()
    else:
        logger.warning('not uploading results to deliverydb, client not available')

    logger.info('Preparing license report')
    _license_report = license_report(upload_results=results)

    return (relevant_results, results_below_threshold, _license_report)


def license_report(
    upload_results: typing.Sequence[UploadResult],
) -> typing.Sequence[typing.Tuple[UploadResult, typing.Set[License]]]:
    def create_component_reports():
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

    return list(create_component_reports())


def filter_and_display_upload_results(
    upload_results: typing.Sequence[UploadResult],
    cvss_version: CVSSVersion,
    cve_threshold=7,
    ignore_if_triaged=True,
) -> typing.Iterable[typing.Tuple[UploadResult, float]]:
    # we only require the analysis_results for now

    results_without_components = []
    results_below_cve_thresh = []
    results_above_cve_thresh = []

    for upload_result in upload_results:
        resource = upload_result.resource

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
            greatest_cve_candidate = highest_major_cve_severity(
                vulnerabilities,
                cvss_version,
            )
            if greatest_cve_candidate > greatest_cve:
                greatest_cve = greatest_cve_candidate

        if greatest_cve >= cve_threshold:
            try:
                # XXX HACK: just one any image ref
                image_ref = resource.access.imageReference
                grafeas_client = ccc.gcp.GrafeasClient.for_image(image_ref)
                gcr_cve = -1
                for r in grafeas_client.filter_vulnerabilities(
                    image_ref,
                    cvss_threshold=cve_threshold,
                ):
                    gcr_cve = max(gcr_cve, r.vulnerability.cvssScore)
                logger.debug(f'gcr says max CVSS=={gcr_cve} (-1 means no vulnerability was found)')
                # TODO: skip if < threshold - just report for now
            except Exception:
                import traceback
                logger.warning(
                    f'failed to retrieve vulnerabilies from gcr {traceback.format_exc()}'
                )

            results_above_cve_thresh.append((upload_result, greatest_cve))
            continue
        else:
            results_below_cve_thresh.append((upload_result, greatest_cve))
            continue

    if results_without_components:
        logger.warning(
            f'Protecode did not identify components for {len(results_without_components)=}:\n'
        )
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
        logger.info(f'The following components were below configured cve threshold {cve_threshold}')
        render_results_table(upload_results=results_below_cve_thresh)
        print('')

    if results_above_cve_thresh:
        logger.warning('The following components have critical vulnerabilities:')
        render_results_table(upload_results=results_above_cve_thresh)

    return results_above_cve_thresh, results_below_cve_thresh


def upload_result_to_license_data(
    upload_result: UploadResult,
    datasource: str = 'protecode-licenses',
) -> dso.model.ComplianceData:

    artefact = dataclasses.asdict(
        upload_result.resource,
        dict_factory=ci.util.dict_factory_enum_serialisiation,
    )

    licenses = list(dict.fromkeys(
        [
            component.license().name()
            for component in upload_result.result.components()
            if component.license()
        ]
    ))
    payload = {
        'licenses': licenses
    }

    compliance_data = dso.model.ComplianceData.create(
        type=datasource,
        artefact=artefact,
        component=upload_result.component,
        data=payload,
    )

    return compliance_data


def upload_result_to_cve_data(
    upload_result: UploadResult,
    greatest_cvss3_score: float,
    datasource: str = dso.model.Datasource.PROTECODE,
) -> dso.model.ComplianceData:

    artefact = dataclasses.asdict(
        upload_result.resource,
        dict_factory=ci.util.dict_factory_enum_serialisiation,
    )

    payload = {
        'greatestCvss3Score': greatest_cvss3_score,
        'protecodeProductUrl': upload_result.result.report_url(),
    }

    compliance_data = dso.model.ComplianceData.create(
        type=datasource,
        artefact=artefact,
        component=upload_result.component,
        data=payload,
    )

    return compliance_data
