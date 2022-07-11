import concourse.steps.scan_sources
import concourse.steps.component_descriptor_util as cdu


def upload_and_scan_from_component_descriptor(
        checkmarx_cfg_name: str,
        component_descriptor_path: str,
        team_id: str=None,
        force: bool=False,
):
    component_descriptor = cdu.component_descriptor_from_component_descriptor_path(
        cd_path=component_descriptor_path,
    )

    concourse.steps.scan_sources.scan_sources(
        checkmarx_cfg_name=checkmarx_cfg_name,
        team_id=team_id,
        component_descriptor=component_descriptor,
        force=force,
    )
