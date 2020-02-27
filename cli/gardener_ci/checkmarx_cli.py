import checkmarx.facade
import checkmarx.client
import product.model
import ci.util


def upload_and_scan_from_component_descriptor(
    checkmarx_cfg_name: str,
    team_id: str,
    component_descriptor: str
):
    component_descriptor = product.model.ComponentDescriptor.from_dict(
        ci.util.parse_yaml_file(component_descriptor)
    )

    for component in component_descriptor.components():
        res = checkmarx.facade.upload_and_scan_repo(checkmarx_cfg_name, team_id, component)

        print(res)
