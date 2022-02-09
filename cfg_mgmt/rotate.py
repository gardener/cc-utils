import logging

import model

logger = logging.getLogger(__name__)


def rotate_cfg_element(
    cfg_dir: str,
    cfg_element: model.NamedModelElement,
):
    type_name = cfg_element._type_name

    if type_name == 'container_registry':
        logger.info(f'would not rotate {type_name=} {cfg_element._name=}')
        return

    logger.warning(f'{type_name=} is not (yet) supported for automated rotation')
