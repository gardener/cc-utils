import typing

import ci.util
import checkmarx.util
import product.model
import product.util
import product.v2
import dso.util
import whitesource.client
import whitesource.component
import whitesource.util

import gci.componentmodel as cm


def scan_sources_and_notify(
    checkmarx_cfg_name: str,
    component_descriptor_path: str,
    email_recipients,
    team_id: str,
    threshold: int = 40,
    exclude_paths: typing.Sequence[str] = (),
    include_paths: typing.Sequence[str] = (),
):
    checkmarx_client = checkmarx.util.create_checkmarx_client(checkmarx_cfg_name)

    scans = checkmarx.util.scan_sources(
        component_descriptor_path=component_descriptor_path,
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
    component_descriptor_path: str,
    extra_whitesource_config: dict,
    requester_mail: str,
    cve_threshold: float,
    notification_recipients: list,
):
    clogger = dso.util.component_logger(__name__)
    clogger.info('creating whitesource client')
    ws_client = whitesource.util.create_whitesource_client(
        whitesource_cfg_name=whitesource_cfg_name,
    )

    clogger.info('parsing component descriptor')
    component_descriptor = cm.ComponentDescriptor.from_dict(
        ci.util.parse_yaml_file(component_descriptor_path)
    )
    components = product.v2.components(component_descriptor_v2=component_descriptor)

    product_name = component_descriptor.component.name

    # get scan artifacts with configured label
    scan_artifacts_gen = whitesource.component._get_scan_artifacts_from_components(components)
    scan_artifacts = tuple(scan_artifacts_gen)
    clogger.info(f'will scan {len(scan_artifacts)} artifacts')

    i = 1
    for scan_artifact in scan_artifacts:
        clogger.info(f'artifact {i} / {len(scan_artifacts)}')
        whitesource.util.scan_artifact_with_white_src(
            extra_whitesource_config=extra_whitesource_config,
            requester_mail=requester_mail,
            scan_artifact=scan_artifact,
            ws_client=ws_client,
        )
        i += 1

    whitesource.util.notify_users(
        notification_recipients=notification_recipients,
        cve_threshold=cve_threshold,
        ws_client=ws_client,
        product_name=product_name,
    )
