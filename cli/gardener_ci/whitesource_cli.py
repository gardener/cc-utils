import json
from json.decoder import JSONDecodeError as JSONDecodeError

import concourse.steps.scan_sources


def upload_and_scan_component(
    whitesource_cfg_name: str,
    product_token: str,
    component_descriptor_path: str,
    requester_mail: str,
    component_name: str,
    extra_whitesource_config: str = {},
    notification_recipients: [str] = [],
    cve_threshold: float = 5.0,
):
    try:
        extra_whitesource_config = json.loads(extra_whitesource_config)
    except JSONDecodeError as e:
        raise e

    concourse.steps.scan_sources.scan_component_with_whitesource(
        whitesource_cfg_name=whitesource_cfg_name,
        product_token=product_token,
        component_descriptor_path=component_descriptor_path,
        extra_whitesource_config=extra_whitesource_config,
        requester_mail=requester_mail,
        cve_threshold=cve_threshold,
        notification_recipients=notification_recipients,
    )
