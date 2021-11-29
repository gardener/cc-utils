import ast

import concourse.steps.component_descriptor_util as util
import concourse.steps.scan_sources
import whitesource.util


def scan_component_from_component_descriptor(
    whitesource_cfg_name: str,
    component_descriptor_path: str,
    notification_recipients: [str] = [],
    filters: str = None,
    extra_whitesource_config: str = None,
    cve_threshold: float = 5.0,
    max_workers=4,
):
    if filters:
        filters = ast.literal_eval(filters)
    if extra_whitesource_config:
        extra_whitesource_config = ast.literal_eval(extra_whitesource_config)

    concourse.steps.scan_sources.scan_component_with_whitesource(
        whitesource_cfg_name=whitesource_cfg_name,
        component_descriptor=util.component_descriptor_from_component_descriptor_path(
            cd_path=component_descriptor_path,
        ),
        extra_whitesource_config=extra_whitesource_config,
        cve_threshold=cve_threshold,
        notification_recipients=notification_recipients,
        max_workers=max_workers,
        filters=filters,
    )


def scan_component_from_ctf(
    whitesource_cfg_name: str,
    ctf_path: str,
    notification_recipients: str = None,
    filters: str = None,
    extra_whitesource_config: str = None,
    cve_threshold: float = 5.0,
    max_workers=4,
):
    if filters:
        filters = ast.literal_eval(filters)
    if extra_whitesource_config:
        extra_whitesource_config = ast.literal_eval(extra_whitesource_config)
    if notification_recipients:
        notification_recipients = ast.literal_eval(notification_recipients)

    concourse.steps.scan_sources.scan_component_with_whitesource(
        whitesource_cfg_name=whitesource_cfg_name,
        component_descriptor=util.component_descriptor_from_ctf_path(
            ctf_path=ctf_path,
        ),
        extra_whitesource_config=extra_whitesource_config,
        cve_threshold=cve_threshold,
        notification_recipients=notification_recipients,
        max_workers=max_workers,
        filters=filters,
    )


def scan_component_from_dir(
    whitesource_cfg_name: str,
    dir_path: str,
    notification_recipients: str = None,
    filters: str = None,
    extra_whitesource_config: str = None,
    cve_threshold: float = 5.0,
    max_workers=4,
):
    if filters:
        filters = ast.literal_eval(filters)
    if extra_whitesource_config:
        extra_whitesource_config = ast.literal_eval(extra_whitesource_config)
    if notification_recipients:
        notification_recipients = ast.literal_eval(notification_recipients)

    concourse.steps.scan_sources.scan_component_with_whitesource(
        whitesource_cfg_name=whitesource_cfg_name,
        component_descriptor=util.component_descriptor_from_dir(
            dir_path=dir_path,
        ),
        extra_whitesource_config=extra_whitesource_config,
        cve_threshold=cve_threshold,
        notification_recipients=notification_recipients,
        max_workers=max_workers,
        filters=filters,
    )


def delete_all_projects_from_product(
    product_token: str,
    user_token: str,
    api_endpoint: str,
):
    whitesource.util.delete_all_projects_from_product(
        product_token=product_token,
        user_token=user_token,
        api_endpoint=api_endpoint,
    )
