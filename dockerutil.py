import logging
import os
import subprocess

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
