import dataclasses

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
        scan_result = checkmarx.project.upload_and_scan_repo(client, team_id, component)
        print(dataclasses.asdict(scan_result))
        checkmarx.util.print_scan_result(scan_result=scan_result)
