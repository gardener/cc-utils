import logging

import cfg_mgmt.gcp as cmg
import cfg_mgmt.model as cmm
import gitutil
import model
import model.github
import oci.model as om


logger = logging.getLogger(__name__)


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

    if type_name == 'container_registry':
        if cfg_element.registry_type() == om.OciRegistryType.GCR:
            logger.info(f'rotating {cfg_element.name()} {type_name=}')
            cmg.rotate_gcr_cfg_element(
                cfg_dir=cfg_dir,
                cfg_element=cfg_element,
                git_helper=git_helper,
                target_ref=target_ref,
                cfg_metadata=cfg_metadata,
            )
            return True
        logger.warning(
            f'{cfg_element.registry_type()} is not (yet) supported for automated rotation'
        )
        return False

    logger.warning(f'{type_name=} is not (yet) supported for automated rotation')
    return False
