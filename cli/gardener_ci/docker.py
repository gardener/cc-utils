import json

import model.container_registry as mcr
import oci.auth as oa


def cfg(
    image_ref_prefixes: [str],
    privileges='readwrite',
):
    cfgs = set()
    if privileges == 'readwrite':
        privileges = oa.Privileges.READWRITE
    elif privileges in ('readonly', 'read'):
        privileges == oa.Privileges.READONLY
    else:
        raise ValueError(privileges)

    for prefix in image_ref_prefixes:
        cfgs.add(mcr.find_config(
                image_reference=prefix,
                privileges=privileges,
            )
        )

    if not cfgs:
        print('did not find any cfg for given prefixes')

    def iter_auths():
        for cfg in cfgs:
            for host, auth in cfg.as_docker_auths().items():
                yield host, auth

    docker_cfg = {
        'auths': dict(iter_auths()),
    }

    print(json.dumps(docker_cfg, indent=2))
