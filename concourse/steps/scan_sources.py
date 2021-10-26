import logging
import typing

import gci.componentmodel as cm

import ccc.whitesource
import checkmarx.util
import cnudie.retrieve
import whitesource.component
import whitesource.util


logger: logging.Logger = logging.getLogger(__name__)


def scan_sources_and_notify(
    checkmarx_cfg_name: str,
    component_descriptor: cm.ComponentDescriptor,
    email_recipients,
    team_id: str,
    threshold: int = 40,
    exclude_paths: typing.Sequence[str] = (),
    include_paths: typing.Sequence[str] = (),
):
    checkmarx_client = checkmarx.util.create_checkmarx_client(checkmarx_cfg_name)

    scans = checkmarx.util.scan_sources(
        component_descriptor=component_descriptor,
        cx_client=checkmarx_client,
        team_id=team_id,
        threshold=threshold,
        exclude_paths=exclude_paths,
        include_paths=include_paths,
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
    component_descriptor: cm.ComponentDescriptor,
    cve_threshold: float,
    extra_whitesource_config: dict = None,
    notification_recipients: list = None,
    filters: list = None,
    max_workers=4,
):
    filters = whitesource.util.parse_filters(filters=filters)

    whitesource_client = ccc.whitesource.make_client(
        whitesource_cfg_name=whitesource_cfg_name,
    )

    components = cnudie.retrieve.components(component=component_descriptor)

    scan_artifacts_gen = whitesource.component.scan_artifacts_gen(
        components,
        filters=filters,
    )
    scan_artifacts = tuple(scan_artifacts_gen)

    exit_codes = whitesource.util.scan_artifacts(
        whitesource_client=whitesource_client,
        extra_whitesource_config=extra_whitesource_config,
        max_workers=max_workers,
        artifacts=scan_artifacts,
    )

    product_name = component_descriptor.component.name

    # if any exit_code != 0, send "scan fail" notification
    if any((lambda: e != 0)() for e in exit_codes):
        logger.warning('some scans failed')
        logger.info(f'notifying {notification_recipients} about failed scan')
        whitesource.util.send_scan_failed(
            notification_recipients=notification_recipients,
            product_name=product_name,
        )
    else:
        logger.info('all scans reported a successful execution')

    projects = whitesource_client.projects_of_product()

    whitesource.util.print_scans(
        cve_threshold=cve_threshold,
        projects=projects,
        product_name=product_name,
    )

    logger.info(f'sending vulnerability report to {notification_recipients}')
    whitesource.util.send_vulnerability_report(
        notification_recipients=notification_recipients,
        cve_threshold=cve_threshold,
        product_name=product_name,
        projects=projects,
    )
