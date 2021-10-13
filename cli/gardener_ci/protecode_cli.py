import logging
import typing

import ci.util
import concourse.steps.component_descriptor_util as component_descriptor_util
import concourse.steps.images
import concourse.steps.scan_container_images
from protecode.model import CVSSVersion
from protecode.util import upload_grouped_images
from protecode.scanning_util import ProcessingMode


logger = logging.getLogger(__name__)


def scan_without_notification(
    protecode_cfg_name: str,
    protecode_api_url: str,
    protecode_group_id: int,
    cvss_version: str,
    component_descriptor_path: str,
    processing_mode: str,
    parallel_jobs: int,
    cve_threshold: float,
    allowed_licenses: typing.List[str] = [],
    prohibited_licenses: typing.List[str] = [],
    reference_protecode_group_ids: typing.List[int] = [],
    include_image_references: typing.List[str] = [],
    exclude_image_references: typing.List[str] = [],
    include_image_names: typing.List[str] = [],
    exclude_image_names: typing.List[str] = [],
    include_component_names: typing.List[str] = [],
    exclude_component_names: typing.List[str] = [],
):
    protecode_group_url = f'{protecode_api_url}/group/{protecode_group_id}/'
    cd = component_descriptor_util.component_descriptor_from_component_descriptor_path(
        cd_path=component_descriptor_path,
    )
    cfg_factory = ci.util.ctx().cfg_factory()
    protecode_cfg = cfg_factory.protecode(protecode_cfg_name)

    filter_function = concourse.steps.images.create_composite_filter_function(
        include_image_references=include_image_references,
        exclude_image_references=exclude_image_references,
        include_image_names=include_image_names,
        exclude_image_names=exclude_image_names,
        include_component_names=include_component_names,
        exclude_component_names=exclude_component_names,
    )

    concourse.steps.scan_container_images.print_protecode_info_table(
        protecode_group_id=protecode_group_id,
        reference_protecode_group_ids=reference_protecode_group_ids,
        protecode_group_url=protecode_group_url,
        cvss_version=CVSSVersion(cvss_version),
        include_image_references=include_image_references,
        exclude_image_references=exclude_image_references,
        include_image_names=include_image_names,
        exclude_image_names=exclude_image_names,
        include_component_names=include_component_names,
        exclude_component_names=exclude_component_names,
    )

    logger.info('running protecode scan for all components')

    results_above_threshold, results_below_threshold, license_report = upload_grouped_images(
        protecode_cfg=protecode_cfg,
        protecode_group_id=protecode_group_id,
        component_descriptor=cd,
        reference_group_ids=reference_protecode_group_ids,
        processing_mode=ProcessingMode(processing_mode),
        parallel_jobs=parallel_jobs,
        cve_threshold=cve_threshold,
        image_reference_filter=filter_function,
        cvss_version=CVSSVersion(cvss_version),
    )

    logger.info('preparing license report for protecode results')
    concourse.steps.scan_container_images.print_license_report(license_report)
    updated_license_report = list(
        concourse.steps.scan_container_images.determine_rejected_licenses(
            license_report,
            allowed_licenses,
            prohibited_licenses,
        )
    )

    logger.info(f'{len(results_above_threshold)=}; {results_above_threshold=}')
    logger.info(f'{len(results_below_threshold)=}; {results_below_threshold=}')
    logger.info(f'{len(updated_license_report)=}; {updated_license_report=}')
    logger.info('finished')
