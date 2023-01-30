import tabulate

import ccc.delivery
import ccc.github
import ccc.oci
import concourse.model.traits.image_scan
import concourse.steps.os_id
import concourse.steps.component_descriptor_util as cdu
import github.compliance.model
import github.compliance.report


__cmd_name__ = 'osinfo'


def os_ids(component_descriptor_path: str):
    component_descriptor = cdu.component_descriptor_from_component_descriptor_path(
        cd_path=component_descriptor_path,
    )

    oci_client = ccc.oci.oci_client()

    results: list[github.compliance.model.OsIdScanResult] = [
        result
        for result in concourse.steps.os_id.determine_os_ids(
            component_descriptor=component_descriptor,
            oci_client=oci_client,
        )
    ]

    def iter_result_tuples():
        for r in results:
            r: github.compliance.model.OsIdScanResult
            c_id = f'{r.scanned_element.component.name}:{r.scanned_element.component.version}'
            a_id = f'{r.scanned_element.resource.name}:{r.scanned_element.resource.version}'
            yield c_id, a_id, r.os_id.PRETTY_NAME, r.os_id.VERSION_ID

    print(tabulate.tabulate(
        iter_result_tuples(),
        headers=['Component ID', 'Artefact ID', 'OS Name', 'OS Version']
    ))
