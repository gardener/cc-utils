import typing

import concourse.steps.scan_sources


def upload_and_scan_component(
    whitesource_cfg_name: str,
    component_descriptor_path: str,
    requester_mail: str,
    notification_recipients: typing.List[str] = [],
    cve_threshold: float = 5.0,
    extra_whitesource_config: typing.Dict = {},
):

    concourse.steps.scan_sources.scan_component_with_whitesource(
        whitesource_cfg_name=whitesource_cfg_name,
        component_descriptor_path=component_descriptor_path,
        extra_whitesource_config=extra_whitesource_config,
        requester_mail=requester_mail,
        cve_threshold=cve_threshold,
        notification_recipients=notification_recipients,
    )
