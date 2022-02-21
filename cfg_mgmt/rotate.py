import dataclasses
import logging
import os
import typing
import yaml

import cfg_mgmt.gcp as cmg
import cfg_mgmt.github as cmgh
import cfg_mgmt.model as cmm
import gitutil
import model
import model.github
import oci.model as om


logger = logging.getLogger(__name__)


def write_config_queue(
    cfg_queue: typing.Iterable[cmm.CfgQueueEntry],
    cfg_queue_file_path: str,
):
    with open(cfg_queue_file_path, 'w') as f:
        yaml.dump(
            {
                'rotation_queue': [
                    dataclasses.asdict(cfg_queue_entry)
                    for cfg_queue_entry in cfg_queue
                ]
            },
            f,
        )


def delete_cfg_element(
    cfg_dir: str,
    cfg_element: str,
    cfg_queue_entry: cmm.CfgQueueEntry,
    cfg_metadata: cmm.CfgMetadata,
    git_helper: gitutil.GitHelper,
    target_ref: str,
):

    delete_func: typing.Callable[[model.NamedModelElement, str, cmm.CfgQueueEntry], None] = None

    if (type_name := cfg_queue_entry.target.type) == 'container_registry':
        if cfg_element.registry_type() == om.OciRegistryType.GCR:
            delete_func = cmg.delete_config_secret
        else:
            f'{cfg_element.registry_type()} is not (yet) supported for automated deletion'
            return

    elif type_name == 'github':
        delete_func = cmgh.delete_config_secret

    if not delete_func:
        logger.warning(
            f'{type_name} is not (yet) supported for automated deletion'
        )
        return

    try:
        delete_func(cfg_element=cfg_element, cfg_dir=cfg_dir, cfg_queue_entry=cfg_queue_entry)
    except Exception as e:
        logger.error(
            f"error deleting secret for cfg-type '{type_name}' with name "
            f"'{cfg_queue_entry.target.name}': {e}."
        )
        raise

    new_cfg_queue: typing.List[cmm.CfgQueueEntry] = [
        entry for entry in cfg_metadata.queue
        if not entry == cfg_queue_entry
    ]
    write_config_queue(
        cfg_queue=new_cfg_queue,
        cfg_queue_file_path=os.path.join(cfg_dir, cmm.cfg_queue_fname),
    )
    git_helper.add_and_commit(
        message=f'process config queue for {cfg_element._type_name}/{cfg_element.name()}',
    )
    try:
        git_helper.push('@', target_ref)
    except:
        logger.warning('failed to push processed config queue - reverting')
        git_helper.repo.git.reset('--hard', '@~')


def rotate_cfg_element(
    cfg_dir: str,
    cfg_element: model.NamedModelElement,
    target_ref: str,
    github_cfg: model.github.GithubConfig,
    cfg_metadata: cmm.CfgMetadata,
    github_repo_path: str,
) -> bool:
    type_name = cfg_element._type_name

    git_helper = gitutil.GitHelper(
        repo=cfg_dir,
        github_cfg=github_cfg,
        github_repo_path=github_repo_path,
    )
    revert_function = typing.Callable[[], None]
    update_secret_function: typing.Callable[
        [str, model.NamedModelElement, cmm.CfgMetadata], revert_function
    ] = None

    if type_name == 'container_registry':
        if cfg_element.registry_type() == om.OciRegistryType.GCR:
            logger.info(f'rotating {cfg_element.name()} {type_name=}')
            update_secret_function = cmg.create_secret_and_persist_in_cfg_repo
        else:
            logger.warning(
                f'{cfg_element.registry_type()} is not (yet) supported for automated rotation'
            )
            return False

    elif type_name == 'github':
        update_secret_function = cmgh.create_secret_and_persist_in_cfg_repo

    if not update_secret_function:
        logger.warning(f'{type_name=} is not (yet) supported for automated rotation')
        return False

    try:
        revert_function = update_secret_function(
            cfg_dir=cfg_dir,
            cfg_element=cfg_element,
            cfg_metadata=cfg_metadata,
        )
    except Exception as e:
        git_helper.repo.git.reset('--hard')
        logger.warning(f'an error occured whilst trying to update secret for {cfg_element=}: {e}')
        return True

    git_helper.add_and_commit(
        message=f'rotate secret for {type_name}/{cfg_element.name()}',
    )
    try:
        git_helper.push('@', target_ref)
    except:
        logger.warning('failed to push updated secret - reverting')
        revert_function()
        git_helper.repo.git.reset('--hard', '@~')

    return True
