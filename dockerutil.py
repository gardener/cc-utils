import logging
import os
import subprocess
import typing

import ci.log
import concourse.paths

logger = logging.getLogger(__name__)
ci.log.configure_default_logging()


def launch_dockerd_if_not_running() -> bool:
    if os.environ.get('DOCKERD_RUNNING', 'no') == (yes := 'yes'):
        logger.info('dockerd already running - early-exiting')
        return False

    logger.info('starting dockerd (may take a few seconds)')
    subprocess.run(
        concourse.paths.launch_dockerd,
        check=True,
    )
    logger.info('successfully launched dockerd')
    # optimisation: only start dockerd once
    os.environ['DOCKERD_RUNNING'] = yes

    return True


def docker_run_argv(
    image_reference: str,
    argv: typing.Iterable[str]=None,
    env: dict=None,
    mounts: dict=None,
) -> tuple[str]:
    docker_argv = ['docker', 'run']

    if env:
        for k, v in env.items():
            docker_argv.extend(('--env', f'{k}={v}'))

    if mounts:
        for host_path, container_path in mounts.items():
            docker_argv.extend(('--volume', f'{host_path}:{container_path}'))

    docker_argv.append(image_reference)

    if argv:
        docker_argv.extend(argv)

    return tuple(docker_argv)
