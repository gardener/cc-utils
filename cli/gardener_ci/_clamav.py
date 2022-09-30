import ccc.clamav
import ccc.delivery
import ccc.oci
import clamav.cnudie
import cnudie.retrieve
import cnudie.iter
import concourse.steps.component_descriptor_util as component_descriptor_util
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
    cd = component_descriptor_util.component_descriptor_from_component_descriptor_path(
        cd_path=component_descriptor_path,
    )
    component = cd.component

    component_descriptor_lookup = cnudie.retrieve.create_default_component_descriptor_lookup(
        default_ctx_repo=component.current_repository_ctx(),
    )

    def to_component_resource_tuple(node: cnudie.iter.ResourceNode):
        return node.component, node.resource

    component_resources = cnudie.iter.iter(
        component=component,
        lookup=component_descriptor_lookup,
        node_filter=cnudie.iter.Filter.resources,
    )

    clamav_client = ccc.clamav.client(url=clamav_url)
    oci_client = ccc.oci.oci_client()

    for result in clamav.cnudie.scan_resources(
        component_resources=(to_component_resource_tuple(node) for node in component_resources),
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
