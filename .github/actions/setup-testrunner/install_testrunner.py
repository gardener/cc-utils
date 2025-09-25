#!/usr/bin/env python

import argparse
import logging
import os
import sys
import tarfile

own_dir = os.path.dirname(__file__)
logger = logging.getLogger('install-testrunner')
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logging.getLogger('oci.client').setLevel(logging.WARN)

try:
    import cnudie.retrieve
except ImportError:
    # patch pythonpath for local development
    sys.path.insert(
        1,
        os.path.join(own_dir, '../../..'),
    )
    import cnudie.retrieve

import oci.auth
import oci.client
import oci.platform
import version
import tarutil


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ocm-repo', default='europe-docker.pkg.dev/gardener-project/releases')
    parser.add_argument('--ocm-component', default='github.com/gardener/test-infra')
    parser.add_argument('--ocm-resource', default='tm-run')
    parser.add_argument('--target-path', default=os.path.join(own_dir, 'testrunner'))
    parser.add_argument('--executable-path', default='testrunner')

    parsed = parser.parse_args()

    oci_client = oci.client.Client(
        credentials_lookup=oci.auth.docker_credentials_lookup(absent_ok=True),
    )

    ocm_repo_lookup = cnudie.retrieve.ocm_repository_lookup(parsed.ocm_repo)
    version_lookup = cnudie.retrieve.version_lookup(
        ocm_repository_lookup=ocm_repo_lookup,
        oci_client=oci_client,
    )
    component_descriptor_lookup = cnudie.retrieve.create_default_component_descriptor_lookup(
        ocm_repository_lookup=ocm_repo_lookup,
        oci_client=oci_client,
    )

    component_name = parsed.ocm_component

    greatest_version = version.greatest_version(
        versions=version_lookup(component_name)
    )
    component = component_descriptor_lookup((
        component_name,
        greatest_version,
    )).component

    logger.info(f'found {greatest_version=}')

    for resource in component.resources:
        if resource.name == parsed.ocm_resource:
            break
    else:
        logger.error(f'did not find {parsed.ocm_resource=} in {component_name}:{greatest_version}')
        exit(1)

    image_ref = resource.access.imageReference

    logger.info(f'found {image_ref=}')

    # currently, this image is publish as a "single-arch" manifest for x86_64 only, so there is no
    # need to check for platform
    oci_manifest = oci_client.manifest(
        image_reference=image_ref,
    )

    path = parsed.executable_path
    outfile = parsed.target_path
    for layer in oci_manifest.layers[-1:]: # hack: only look in last layer
        blob = oci_client.blob(image_reference=image_ref, digest=layer.digest)

        with tarfile.open(
            fileobj=tarutil.FilelikeProxy(generator=blob.iter_content(chunk_size=4096)),
            mode='r|*',
        ) as tf:
            for info in tf:
                if path.removeprefix('/') != info.name.removeprefix('/'):
                    continue
                break
            else:
                continue

            if not info.isfile():
                logger.error(f'{path=} is not a regular file')
                exit(1)

            with open(outfile, 'wb') as f:
                octects_left = info.size
                while octects_left:
                    read = min(octects_left, 4096)
                    f.write(tf.fileobj.read(read))
                    octects_left -= read

            # stop after first match
            break
    else:
        logger.error(f'Did not find {parsed.executable_path=}')
        exit(1)

    os.chmod(outfile, mode=0o744) # set executable bit
    logger.info(f'wrote testrunner to {outfile}')


if __name__ == '__main__':
    main()
