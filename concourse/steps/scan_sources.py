import concurrent.futures
import functools
import traceback

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

    failed_sentinel = object()

    def try_scanning(component):
        try:
            return scan_func(component)
        except:
            traceback.print_exc()
            return failed_sentinel

    scan_results = []
    for scan_result in executor.map(scan_func, component_descriptor.components()):
        if scan_result is failed_sentinel:
            print('XXX scan failed (will not show in table)')
            continue

        scan_results.append(scan_result)

    # XXX raise if an error occurred?
    checkmarx.util.print_scan_result(scan_results=scan_results)
