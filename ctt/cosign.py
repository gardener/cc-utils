# SPDX-FileCopyrightText: 2022 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import logging
import os
import subprocess
import tempfile

import ci.log
import ci.util
import oci.model as om


ci.log.configure_default_logging()
logger = logging.getLogger(__name__)


def attach_signature(
    image_ref: str,
    unsigned_payload: bytes,
    signature: bytes,
    cosign_repository=None,
):
    '''
    attach a cosign signature to an image in a remote oci registry.
    '''
    with tempfile.NamedTemporaryFile('wb') as payloadfile, tempfile.NamedTemporaryFile('wb') as signaturefile:
        payloadfile.write(unsigned_payload)
        payloadfile.flush()

        signaturefile.write(signature)
        signaturefile.flush()

        env = None
        if cosign_repository:
            env = os.environ.copy()
            env['COSIGN_REPOSITORY'] = cosign_repository

        cmd = [
            'cosign',
            'attach',
            'signature',
            '--payload',
            payloadfile.name,
            '--signature',
            signaturefile.name,
            image_ref
        ]

        logger.info(f'run cmd \'{cmd}\'')
        subprocess.run(cmd, env=env, check=True)


def calc_cosign_sig_ref(
    image_ref: str,
) -> str:
    '''
    calculate the image reference of the cosign signature for a specific image.
    '''
    parsed_image_ref = om.OciImageReference.to_image_ref(image_ref)
    if not parsed_image_ref.has_digest_tag:
        ValueError('only images that are referenced via digest are allowed')

    parsed_digest = parsed_image_ref.parsed_digest_tag
    alg, val = parsed_digest
    cosign_sig_ref = f'{parsed_image_ref.ref_without_tag}:{alg}-{val}.sig'

    return cosign_sig_ref
