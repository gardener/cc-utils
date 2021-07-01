import logging
import typing

import checkmarx.util
import gci.componentmodel as cm
import whitesource.util


logger: logging.Logger = logging.getLogger(__name__)


def scan_sources_and_notify(
    checkmarx_cfg_name: str,
    compliancedb_cfg_name: str,
    component_descriptor: cm.ComponentDescriptor,
    email_recipients,
    team_id: str,
    threshold: int = 40,
    exclude_paths: typing.Sequence[str] = (),
    include_paths: typing.Sequence[str] = (),
):

    checkmarx_client = checkmarx.util.create_checkmarx_client(checkmarx_cfg_name)

    scan_artifacts = checkmarx.util.scan_artifacts_from_component_descriptor(
        component_descriptor=component_descriptor,
    )

    scans = checkmarx.util.scan_sources(
        artifacts=scan_artifacts,
        exclude_paths=exclude_paths,
        include_paths=include_paths,
        cx_client=checkmarx_client,
        team_id=team_id,
        threshold=threshold,
    )

    checkmarx.util.insert_results(
        scans=scans,
        scan_artifacts=tuple(scan_artifacts),
        compliancedb_cfg_name=compliancedb_cfg_name,
    )

    checkmarx.util.print_scans(
        scans=scans,
        routes=checkmarx_client.routes,
    )

    checkmarx.util.send_mail(
        scans=scans,
        threshold=threshold,
        email_recipients=email_recipients,
        routes=checkmarx_client.routes,
    )
    #TODO codeowner recipient


def scan_component_with_whitesource(
    whitesource_cfg_name: str,
    compliancedb_cfg_name: str,
    component_descriptor: cm.ComponentDescriptor,
    cve_threshold: float,
    extra_whitesource_config: dict = None,
    notification_recipients: list = None,
    filters: list = None,
    max_workers=4,
):

    filters = whitesource.util.parse_filters(filters=filters)

    whitesource_client = whitesource.util.create_whitesource_client(
        whitesource_cfg_name=whitesource_cfg_name,
    )

    scan_artifacts = whitesource.util.scan_artifacts_from_component_descriptor(
        filters=filters,
        component_descriptor=component_descriptor,
    )

    whitesource.util.scan_artifacts(
        whitesource_client=whitesource_client,
        extra_whitesource_config=extra_whitesource_config,
        max_workers=max_workers,
        scan_artifacts=scan_artifacts,
    )

    product = whitesource.util.product_for_component_descriptor(
        whitesource_client=whitesource_client,
        component_descriptor=component_descriptor,
    )

    whitesource.util.insert_results(
        projects=product.projects,
        scan_artifacts=scan_artifacts,
        compliancedb_cfg_name=compliancedb_cfg_name,
    )

    whitesource.util.print_product_scans(
        cve_threshold=cve_threshold,
        product=product,
    )

    whitesource.util.send_mail(
        notification_recipients=notification_recipients,
        cve_threshold=cve_threshold,
        product=product,
    )
