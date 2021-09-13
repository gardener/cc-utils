import logging
import json
import os
import subprocess
import tempfile
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
    cfg_dir: str=None,
) -> tuple[str]:
    docker_argv = ['docker']

    if cfg_dir:
        docker_argv.extend(('--config', cfg_dir))

    docker_argv.append('run')

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


def mk_docker_cfg_dir(
    cfg: dict,
    cfg_dir: str=None,
    exist_ok=False,
) -> str:
    '''
    creates a directory containing a `config.json` file as expected by docker
    the directory path is returned.
    if exist_ok evaluates to True, an existing `config.json` file will be overwritten.
    '''
    if cfg_dir and not exist_ok and os.path.exists(cfg_dir):
        raise RuntimeError(f'{cfg_dir=} must not exist')

    if not cfg_dir:
        cfg_dir = tempfile.mkdtemp() # cleanup must be done by caller

    docker_cfg_path = os.path.join(cfg_dir, 'config.json')

    with open(docker_cfg_path, 'w') as f:
        json.dump(cfg, f)

    return cfg_dir
