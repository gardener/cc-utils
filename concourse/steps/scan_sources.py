import concurrent.futures


import ci.util
import checkmarx.project
import checkmarx.util
import product.model


def scan_sources(
        checkmarx_cfg_name: str,
        team_id: str,
        component_descriptor: str,
        max_workers: int = 8,
):
    component_descriptor = product.model.ComponentDescriptor.from_dict(
        ci.util.parse_yaml_file(component_descriptor)
    )

    client = checkmarx.util.create_checkmarx_client(checkmarx_cfg_name)

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)

    scan_job_args = []
    for component in component_descriptor.components():
        scan_job_args.append((client, team_id, component))

    scan_results = executor.map(checkmarx.project.upload_and_scan_repo, scan_job_args)

    for scan_result in scan_results:
        checkmarx.util.print_scan_result(scan_result=scan_result)
