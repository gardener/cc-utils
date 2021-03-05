import json

import dockerfile_parse

import model.container_registry as mc


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
