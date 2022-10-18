import gci.componentmodel as cm

import ccc.clamav
import ccc.delivery
import ccc.oci
import ci.util
import clamav.cnudie
import cnudie.retrieve
import cnudie.iter
import dso.model


__cmd_name__ = 'clamav'


def scan_component(
    component_descriptor_path: str,
    clamav_url: str,
    max_worker: int = 16,
):
    '''
    send component to clamav scanner and write results to stdout.
    '''
    component = cm.ComponentDescriptor.from_dict(
        ci.util.parse_yaml_file(component_descriptor_path)
    ).component

    component_descriptor_lookup = cnudie.retrieve.create_default_component_descriptor_lookup(
        default_ctx_repo=component.current_repository_ctx(),
    )

    resource_nodes = cnudie.iter.iter(
        component=component,
        lookup=component_descriptor_lookup,
        node_filter=cnudie.iter.Filter.resources,
    )

    clamav_client = ccc.clamav.client(url=clamav_url)
    oci_client = ccc.oci.oci_client()

    for result in clamav.cnudie.scan_resources(
        resource_nodes=resource_nodes,
        oci_client=oci_client,
        clamav_client=clamav_client,
        max_workers=max_worker,
    ):
        findings_data = clamav.cnudie.resource_scan_result_to_artefact_metadata(
            resource_scan_result=result,
            datasource=dso.model.Datasource.CLAMAV,
            datatype=dso.model.Datatype.MALWARE,
        )
        print(f'{findings_data=}')


def scan_file(
    file_path: str,
    clamav_url: str,
):
    '''
    send file content to clamav scanner and write results to stdout.
    '''

    clamav_client = ccc.clamav.client(url=clamav_url)
    with open(file_path, 'rb') as f:
        contents = f.readlines()

    scan_result = clamav_client.scan(
        data=contents,
    )

    print(scan_result)
