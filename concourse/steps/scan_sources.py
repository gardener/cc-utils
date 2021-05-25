import logging
import os
import tarfile
import typing

import checkmarx.util
import ci.util
import cnudie.util
import gci.componentmodel as cm
import whitesource.client
import whitesource.component
import whitesource.util


logger: logging.Logger = logging.getLogger(__name__)


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

    cds = _path_to_component_descriptors(path=component_descriptor_path)
    if len(cds) > 1:
        raise RuntimeError(
            f'More than one component_descriptor found in {component_descriptor_path}'
        )

    scans = checkmarx.util.scan_sources(
        component_descriptor=cds[0],
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
    cve_threshold: float,
    notification_recipients: list,
    max_workers=4,
):
    whitesource_client = whitesource.util.create_whitesource_client(
        whitesource_cfg_name=whitesource_cfg_name,
    )

    cds = _path_to_component_descriptors(path=component_descriptor_path)
    print(cds)
    exit(1)
    if len(cds) > 1:
        raise RuntimeError(
            f'More than one component_descriptor found in {component_descriptor_path}'
        )

    product_name, projects = whitesource.util.scan_sources(
        whitesource_client=whitesource_client,
        component_descriptor=cds[0],
        extra_whitesource_config=extra_whitesource_config,
        max_workers=max_workers,
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


def _path_to_component_descriptors(path: str) -> typing.List[cm.ComponentDescriptor]:

    if not os.path.isfile(path):
        raise RuntimeError(
            f'Neither component descriptor nor CTX archive found at {path}'
        )

    # path is ctf archive
    if tarfile.is_tarfile(path):
        component_descriptors: typing.List[cm.ComponentDescriptor] = [
            cd
            for cd in cnudie.util.component_descriptors_from_ctf_archive(path)
        ]
        if len(component_descriptors) == 0:
            raise RuntimeError(
                f'No component descriptor found in ctf archive at {path}'
            )

        return component_descriptors

    # path is cd
    else:
        return [
            cm.ComponentDescriptor.from_dict(
                ci.util.parse_yaml_file(path)
            )
        ]
