import logging

import cfg_mgmt.gcp as cmg
import cfg_mgmt.model as cmm
import gitutil
import model
import model.github
import oci.model as om


logger = logging.getLogger(__name__)


def rotate_cfg_element(
    cfg_factory,
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

    update_secret_function = None

    if type_name == 'container_registry':
        if cfg_element.registry_type() == om.OciRegistryType.GCR:
            logger.info(f'rotating {cfg_element.name()} {type_name=}')
            update_secret_function = cmg.create_secret_and_persist_in_cfg_repo
        else:
            logger.warning(
                f'{cfg_element.registry_type()} is not (yet) supported for automated rotation'
            )
            return False

    if not update_secret_function:
        logger.warning(f'{type_name=} is not (yet) supported for automated rotation')
        return False

    try:
        revert_function = update_secret_function(
            cfg_dir=cfg_dir,
            cfg_element=cfg_element,
            target_ref=target_ref,
            cfg_metadata=cfg_metadata,
        )
    except:
        git_helper.repo.git.reset('--hard')
        logger.warning('an error occured whilst trying to update secret for {cfg_element=}')
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
