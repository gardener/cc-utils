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

    success_count = 0
    failed_count = 0
    components_count = len(tuple(component_descriptor.components()))

    def try_scanning(component):
        nonlocal failed_count
        nonlocal success_count
        nonlocal failed_sentinel

        try:
            result = scan_func(component)
            success_count += 1
            print(f'{component.name()=} {result=}')
            return result
        except:
            failed_count += 1
            traceback.print_exc()
            return failed_sentinel

    print(f'will scan {components_count} component(s)')

    scan_results = []
    for scan_result in executor.map(try_scanning, component_descriptor.components()):
        if not scan_result is failed_sentinel:
            scan_results.append(scan_result)
        remaining = components_count - (success_count + failed_count)
        print(f'{remaining=}')

    # XXX raise if an error occurred?
    checkmarx.util.print_scan_result(scan_results=scan_results)

    print(f'{success_count=} / {components_count=}')
