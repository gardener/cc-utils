import itertools
import logging
import typing

import ccc.protecode
import ci.util
import concourse.steps.component_descriptor_util as component_descriptor_util
import concourse.steps.images
import concourse.steps.scan_container_images
from protecode.model import CVSSVersion
from protecode.util import upload_grouped_images as _upload_grouped_images


__cmd_name__ = 'protecode'
logger = logging.getLogger(__name__)


def scan_without_notification(
    protecode_cfg_name: str,
    protecode_group_id: str,
    component_descriptor_path: str,
    cve_threshold: float=7.0,
    protecode_api_url=None,
    reference_protecode_group_ids: typing.List[int] = [],
):
    cfg_factory = ci.util.ctx().cfg_factory()
    protecode_cfg = cfg_factory.protecode(protecode_cfg_name)

    if not protecode_api_url:
        protecode_api_url = protecode_cfg.api_url()
        logger.info(f'Using Protecode at: {protecode_api_url}')

    protecode_group_url = f'{protecode_api_url}/group/{protecode_group_id}/'
    cd = component_descriptor_util.component_descriptor_from_component_descriptor_path(
        cd_path=component_descriptor_path,
    )

    protecode_api_url = protecode_cfg.api_url()
    protecode_group_url = ci.util.urljoin(protecode_api_url, 'group', str(protecode_group_id))

    cvss_version = CVSSVersion.V3

    concourse.steps.scan_container_images.print_protecode_info_table(
        protecode_group_id=protecode_group_id,
        reference_protecode_group_ids=reference_protecode_group_ids,
        protecode_group_url=protecode_group_url,
        cvss_version=cvss_version,
        include_image_references=[],
        exclude_image_references=[],
        include_image_names=[],
        exclude_image_names=[],
        include_component_names=[],
        exclude_component_names=[],
    )

    logger.info('running protecode scan for all components')

    results = _upload_grouped_images(
        protecode_cfg=protecode_cfg,
        protecode_group_id=protecode_group_id,
        component_descriptor=cd,
    )

    results_above_threshold = [r for r in results if r.greatest_cve_score >= cve_threshold]
    results_below_threshold = [r for r in results if r.greatest_cve_score < cve_threshold]

    logger.info('Summary of found vulnerabilities:')
    logger.info(f'{len(results_above_threshold)=}; {results_above_threshold=}')
    logger.info(f'{len(results_below_threshold)=}; {results_below_threshold=}')


def transport_triages(
    protecode_cfg_name: str,
    from_product_id: int,
    to_group_id: int,
    to_product_ids: [int],
):
    cfg_factory = ci.util.ctx().cfg_factory()
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
    logger.info(f'found {len(triages)} triage(s) to import')

    for to_product_id, component_name_and_triage in itertools.product(to_product_ids, triages):
        component, triage = component_name_and_triage
        for target_component_version in target_component_versions(
            product_id=to_product_id,
            component_name=component.name(),
        ):
            logger.info(f'adding triage for {triage.component_name()}:{target_component_version}')
            api.add_triage(
                triage=triage,
                product_id=to_product_id,
                group_id=to_group_id,
                component_version=target_component_version,
            )
        logger.info(f'added triage for {triage.component_name()} to {to_product_id}')
