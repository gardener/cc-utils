import concourse.steps.scan_sources


def upload_and_scan_from_component_descriptor(
        checkmarx_cfg_name: str,
        team_id: str,
        component_descriptor_path: str
):
    concourse.steps.scan_sources.scan_sources_and_notify(
        checkmarx_cfg_name=checkmarx_cfg_name,
        team_id=team_id,
        component_descriptor_path=component_descriptor_path,
        email_recipients=['johannes.krayl@sap.com']
    )
