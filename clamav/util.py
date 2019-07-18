import logging
import os
import shutil
import subprocess
import tarfile
import typing

import clamd

import container.registry

logger = logging.getLogger(__name__)

# XXX hard-code for now (see Dockerfile / res/clamd.conf)
_clamd_sock = '/run/clamav/clamd.sock'


def init_daemon():
    if os.path.exists(_clamd_sock):
        return # assume deaom is alrady running

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


def scan_stream(fileobj):
    c = clamd_client()

    result = c.instream(fileobj)

    if not len(result) == 1 or not 'stream' in result:
        # expected format: {"stream": (<status>, <signature-name|None>)}
        raise RuntimeError(f'result does not meet expected format: {result}')

    status, signature_or_none = result['stream']
    return status, signature_or_none


def iter_image_files(
    container_image_reference: str,
) -> typing.Iterable[typing.Tuple[typing.IO, str]]:
    with tarfile.open(
        mode='r|',
        fileobj=container.registry.retrieve_container_image(container_image_reference)
    ) as image_tarfile:
        for image_tar_info in image_tarfile:
            # we only care to scan files, obviously
            if not image_tar_info.isfile():
                continue
            if not image_tar_info.name.endswith('layer.tar'):
                continue # only layer files may contain relevant data
            with tarfile.open(
                mode='r|',
                fileobj=image_tarfile.extractfile(image_tar_info),
            ) as layer_tarfile:
                for layer_tar_info in layer_tarfile:
                    if not layer_tar_info.isfile():
                        continue
                    yield (
                        layer_tarfile.extractfile(layer_tar_info),
                        f'{image_tar_info.name}:{layer_tar_info.name}',
                    )


def scan_container_image(
    image_reference: str,
):
    logger.debug(f'scanning container image {image_reference}')
    for content, path in iter_image_files(image_reference):
        status, signature = scan_stream(content)
        if result_ok(status, signature):
            continue
        else:
            return status, f'{path}: {signature}'
    return 'OK', None


def result_ok(status, signature):
    if status == 'OK':
        return True
    return False
