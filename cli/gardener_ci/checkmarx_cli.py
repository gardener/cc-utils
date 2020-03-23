import ci.util
import checkmarx.client
import checkmarx.project
import checkmarx.util
import product.model


def upload_and_scan_from_component_descriptor(
        checkmarx_cfg_name: str,
        team_id: str,
        component_descriptor: str
):
    component_descriptor = product.model.ComponentDescriptor.from_dict(
        ci.util.parse_yaml_file(component_descriptor)
    )

    for component in component_descriptor.components():
        client = checkmarx.util.create_checkmarx_client(checkmarx_cfg_name)
        scan_result = checkmarx.project.upload_and_scan_repo(
            checkmarx_client=client,
            team_id=team_id,
            component=component,
        )
        checkmarx.util.print_scan_result(scan_results=[scan_result])
