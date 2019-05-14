import logging
import os
import shutil
import subprocess

import clamd


logger = logging.getLogger(__name__)

# XXX hard-code for now (see Dockerfile / res/clamd.conf)
_clamd_sock = '/run/clamav/clamd.sock'


def init_daemon():
    if os.path.exists(_clamd_sock):
        return logger.info('clamd already running')

    # ensure runtime dependencies (we require clamav/clamd to be installed)
    fresh_clam = shutil.which('freshclam')
    if not fresh_clam:
        raise RuntimeError('fresh_clam must be available from PATH')

    logger.info("updating ClamAV's virus signature DB - this may take a while")

    subprocess.run(
        [fresh_clam],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False, # 1 is returned if clamav is outdated - ignore for now
    )

    logger.info('done updating virus signature DB')

    clamd_executable = shutil.which('clamd')
    if not clamd_executable:
        raise RuntimeError('clamd must be available from PATH')

    logger.info('starting clamd - this may take a while')
    subprocess.run(
        [clamd_executable],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )


def clamd_client():
    init_daemon()

    client = clamd.ClamdUnixSocket(_clamd_sock)
    # smoke-test
    client.ping()

    return client
