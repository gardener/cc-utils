import json

import concourse.steps.scan_sources


def upload_and_scan_component(
    whitesource_cfg_name: str,
    component_descriptor_path: str,
    requester_mail: str,
    cve_threshold: float = 5.0,
    notification_recipients: str = '[]',
    extra_whitesource_config: str = '{}',
    chunk_size: int = 1024,
    ping_interval: int = 1000,
    ping_timeout: int = 1000,
):

    # parse sequence strings to actual sequences since when provided by cli only strings are possible
    notification_recipients = notification_recipients.split(',')
    extra_whitesource_config = json.loads(extra_whitesource_config)

    concourse.steps.scan_sources.scan_component_with_whitesource(
        whitesource_cfg_name=whitesource_cfg_name,
        component_descriptor_path=component_descriptor_path,
        extra_whitesource_config=extra_whitesource_config,
        requester_mail=requester_mail,
        cve_threshold=cve_threshold,
        notification_recipients=notification_recipients,
        chunk_size=chunk_size,
        ping_interval=ping_interval,
        ping_timeout=ping_timeout,
    )
