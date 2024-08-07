import json
import os

import model.container_registry as mcr
import oci.auth as oa


def cfg(
    image_ref_prefixes: [str],
    privileges: str ='readwrite',
    patch: bool=False,
):
    cfgs = set()
    if privileges == 'readwrite':
        privileges = oa.Privileges.READWRITE
    elif privileges in ('readonly', 'read'):
        privileges = oa.Privileges.READONLY
    else:
        raise ValueError(privileges)

    for prefix in image_ref_prefixes:
        cfg = mcr.find_config(
            image_reference=prefix,
            privileges=privileges,
        )
        if not cfg:
            continue

        cfgs.add(cfg)

    if not cfgs:
        print(f'did not find any cfg for given prefixes {image_ref_prefixes=}')
        exit(1)

    def iter_auths():
        for cfg in cfgs:
            for host, auth in cfg.as_docker_auths().items():
                yield host, auth

    docker_cfg = {
        'auths': dict(iter_auths()),
    }

    if not patch:
        print(json.dumps(docker_cfg, indent=2))
        exit(0)

    with open(os.path.join(os.environ['HOME'], '.docker', 'config.json'), 'w') as f:
        json.dump(docker_cfg, f)
