import logging
import typing

import concourse.steps.component_descriptor_util as util
import concourse.steps.scan_sources


logger: logging.Logger = logging.getLogger(__name__)


def scan_component_from_component_descriptor(
    whitesource_cfg_name: str,
    component_descriptor_path: str,
    notification_recipients: [str],
    cve_threshold: float = 5.0,
    extra_whitesource_config: typing.Dict={},
    max_workers=4,
):
    concourse.steps.scan_sources.scan_component_with_whitesource(
        whitesource_cfg_name=whitesource_cfg_name,
        component_descriptor=util.component_descriptor_from_component_descriptor_path(
            cd_path=component_descriptor_path,
        ),
        extra_whitesource_config=extra_whitesource_config,
        cve_threshold=cve_threshold,
        notification_recipients=notification_recipients,
        max_workers=max_workers,
    )


def scan_component_from_ctf(
    whitesource_cfg_name: str,
    ctf_path: str,
    notification_recipients: [str],
    cve_threshold: float = 5.0,
    extra_whitesource_config: typing.Dict={},
    max_workers=4,
):
    concourse.steps.scan_sources.scan_component_with_whitesource(
        whitesource_cfg_name=whitesource_cfg_name,
        component_descriptor=util.component_descriptor_from_ctf_path(
            ctf_path=ctf_path,
        ),
        extra_whitesource_config=extra_whitesource_config,
        cve_threshold=cve_threshold,
        notification_recipients=notification_recipients,
        max_workers=max_workers,
    )


def scan_component_from_dir(
    whitesource_cfg_name: str,
    dir_path: str,
    notification_recipients: [str],
    cve_threshold: float = 5.0,
    extra_whitesource_config: typing.Dict={},
    max_workers=4,
):
    concourse.steps.scan_sources.scan_component_with_whitesource(
        whitesource_cfg_name=whitesource_cfg_name,
        component_descriptor=util.component_descriptor_from_dir(
            dir_path=dir_path,
        ),
        extra_whitesource_config=extra_whitesource_config,
        cve_threshold=cve_threshold,
        notification_recipients=notification_recipients,
        max_workers=max_workers,
    )
