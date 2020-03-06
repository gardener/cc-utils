import concurrent.futures
import functools

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

    scan_func = functools.partial(
            checkmarx.project.upload_and_scan_repo,
            checkmarx_client=client,
            team_id=team_id,
    )

    scan_results = []
    try:
        for scan_result in executor.map(scan_func, component_descriptor.components()):
            scan_results.append(scan_result)

        checkmarx.util.print_scan_result(scan_results=scan_results)
    except:
        # at least print what we have so far
        checkmarx.util.print_scan_result(scan_results=scan_results)
        raise
