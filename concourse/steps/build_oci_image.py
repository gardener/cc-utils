import json
import logging
import subprocess

import dockerfile_parse

import ci.log
import model.container_registry as mc

ci.log.configure_default_logging()
logger = logging.getLogger('build-oci-image.step')


def write_docker_cfg(
    dockerfile_path: str,
    docker_cfg_path: str
):
    with open(dockerfile_path) as f:
        parser = dockerfile_parse.DockerfileParser(fileobj=f)
        relevant_image_refs = parser.parent_images

    # use dict to deduplicate by cfg-name (which we otherwise do not care about)
    container_registry_cfgs = {
        c.name(): c for c
        in (mc.find_config(img_ref) for img_ref in relevant_image_refs)
        if c is not None
    }.values()

    docker_cfg_auths = {}

    for container_registry_cfg in container_registry_cfgs:
        docker_cfg_auths.update(container_registry_cfg.as_docker_auths())

    docker_cfg = {'auths': docker_cfg_auths}

    with open(docker_cfg_path, 'w') as f:
        json.dump(docker_cfg, f)


def prepare_qemu_and_binfmt_misc():
    ## needs to be run once to allow for cross-platform executions/builds
    ## see: https://github.com/multiarch/qemu-user-static
    subprocess.run((
        'docker',
        'run',
        '--rm',
        '--privileged',
        'multiarch/qemu-user-static',
        '--reset',
        '--persistent', 'yes',
    ))
