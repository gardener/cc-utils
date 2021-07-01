import logging

import ci.util
import concourse.steps.images
import concourse.steps.component_descriptor_util as util
from protecode.model import CVSSVersion
import protecode.scanning_util
import protecode.util


logger: logging.Logger = logging.getLogger(__name__)


def scan_component_from_component_descriptor(
    protecode_cfg_name: str,
    component_descriptor_path: str,
    parallel_jobs: int = 1,
    compliancedb_cfg_name: str = None,
    processing_mode: str = 'rescan',
    protecode_group_id: str = '5',
    cvss_version: str = 'CVSSv3',
    cve_threshold: float = 5.0,
):

    cfg_factory = ci.util.ctx().cfg_factory()
    protecode_cfg = cfg_factory.protecode(protecode_cfg_name)

    filter_function = concourse.steps.images.create_composite_filter_function(
        include_image_references=('eu.gcr.*',),
        exclude_image_references=(),
        include_image_names=(),
        exclude_image_names=(),
        include_component_names=(),
        exclude_component_names=(),
    )

    protecode_report = protecode.util.upload_grouped_images(
        protecode_cfg=protecode_cfg,
        protecode_group_id=protecode_group_id,
        component_descriptor=util.component_descriptor_from_component_descriptor_path(
            cd_path=component_descriptor_path,
        ),
        reference_group_ids=(),
        processing_mode=protecode.scanning_util.ProcessingMode(processing_mode),
        parallel_jobs=parallel_jobs,
        cve_threshold=cve_threshold,
        image_reference_filter=filter_function,
        cvss_version=CVSSVersion(cvss_version),
    )

    protecode.scanning_util.insert_results(
        protecode_results=protecode_report.rawResults,
        compliancedb_cfg_name=compliancedb_cfg_name,
    )
