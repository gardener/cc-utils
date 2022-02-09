import logging

import model

logger = logging.getLogger(__name__)


def rotate_cfg_element(
    cfg_dir: str,
    cfg_element: model.NamedModelElement,
) -> bool:
    type_name = cfg_element._type_name

    if type_name == 'container_registry':
        logger.info(f'would now rotate {type_name=} {cfg_element._name=}')
        return True

    logger.warning(f'{type_name=} is not (yet) supported for automated rotation')
    return False
