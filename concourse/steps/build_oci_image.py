import json
import logging
import os
import shutil
import sys

import dockerfile_parse

import ci.log
import model.container_registry as mc

ci.log.configure_default_logging()
logger = logging.getLogger('kaniko-build.step')


_rescue_root_path = '/kaniko/rescued-fs'


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


def mv_directories_to_kaniko_dir(
    skip_dirnames=('dev', 'proc', 'sys', 'etc', 'media', 'run', 'srv', 'kaniko', 'scratch'),
    tgt_dir=_rescue_root_path,
):
    mv_dirnames = [
        d for d in os.listdir('/')
        if os.path.isdir(os.path.join('/', d)) and not d in skip_dirnames
    ]
    logger.info(f'mv {mv_dirnames=} to {tgt_dir=} to prepare kaniko-build')

    for src_dirname in mv_dirnames:
        src_path = os.path.join('/', src_dirname)
        tgt_path = os.path.join(tgt_dir, src_dirname)

        if os.path.exists(tgt_path):
            logger.error(f'{tgt_path=} exists - this should not happen')

        shutil.move(src_path, tgt_path)

    _fix_python_path(root_dir=tgt_dir)

    return tgt_dir


def _fix_python_path(
    root_dir=_rescue_root_path,
):
    if sys.version_info.major < 3:
        raise NotImplementedError('we require python3 or greater')

    for idx, path in enumerate(sys.path):
        if not os.path.isabs(path):
            continue

        # prepend new root_dir prefix (must strip leading /, otherwise join will not prepend)
        patched_path = os.path.join(root_dir, path[1:])
        sys.path[idx] = patched_path

    logger.info(f'patched pythonpath to {sys.path=}')


def restore_required_dirs(
    root_dir=_rescue_root_path,
):
    '''
    restores a (hardcoded) set of directories from "rescue-dir" back to original location
    '''

    build_dir_relpath = 'tmp/build'
    build_dir_src = os.path.join(root_dir, build_dir_relpath)
    build_dir_tgt = os.path.join('/', build_dir_relpath)

    shutil.move(build_dir_src, build_dir_tgt)
    for dirname in ('usr', 'lib', 'bin'):
        shutil.move(os.path.join(root_dir, dirname), os.path.join('/', dirname))
