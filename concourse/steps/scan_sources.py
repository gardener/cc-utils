import logging
import typing

import checkmarx.util
import gci.componentmodel as cm
import whitesource.client
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

    whitesource_client = whitesource.util.create_whitesource_client(
        whitesource_cfg_name=whitesource_cfg_name,
    )

    product_name, projects = whitesource.util.scan_sources(
        whitesource_client=whitesource_client,
        component_descriptor=component_descriptor,
        extra_whitesource_config=extra_whitesource_config,
        max_workers=max_workers,
        filters=filters,
    )

    whitesource.util.print_scans(
        cve_threshold=cve_threshold,
        projects=projects,
        product_name=product_name,
    )

    whitesource.util.send_mail(
        notification_recipients=notification_recipients,
        cve_threshold=cve_threshold,
        product_name=product_name,
        projects=projects,
    )
