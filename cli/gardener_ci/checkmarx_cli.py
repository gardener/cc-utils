import concourse.steps.scan_sources


def upload_and_scan_from_component_descriptor(
        checkmarx_cfg_name: str,
        team_id: str,
        component_descriptor: str
):
    # component_descriptor = product.model.ComponentDescriptor.from_dict(
    #     ci.util.parse_yaml_file(component_descriptor)
    # )

    concourse.steps.scan_sources.scan_sources_and_notify(
        checkmarx_cfg_name=checkmarx_cfg_name,
        team_id=team_id,
        component_descriptor=component_descriptor,
        email_recipients=['johannes.krayl@sap.com']
    )

    # for component in component_descriptor.components():
    #     client = checkmarx.util.create_checkmarx_client(checkmarx_cfg_name)
    #     scan_result = checkmarx.project.upload_and_scan_repo(
    #         checkmarx_client=client,
    #         team_id=team_id,
    #         component=component,
    #     )
    #     checkmarx.util.print_scan_result(scan_results=[scan_result])
