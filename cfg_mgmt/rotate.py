import logging
import typing

import cfg_mgmt
import cfg_mgmt.aws as cmaws
import cfg_mgmt.alicloud as cmali
import cfg_mgmt.azure as cma
import cfg_mgmt.btp_application_certificate as cmbac
import cfg_mgmt.btp_service_binding as cmb
import cfg_mgmt.gcp as cmg
import cfg_mgmt.github as cmgh
import cfg_mgmt.kubernetes as cmk
import cfg_mgmt.model as cmm
import model
import model.github
import oci.model as om


logger = logging.getLogger(__name__)


def delete_expired_secret(
    cfg_element: model.NamedModelElement,
    cfg_queue_entry: cmm.CfgQueueEntry,
    cfg_factory: model.ConfigFactory,
) -> tuple[model.NamedModelElement, bool]:
    '''Deletes the expired secret contained in the given cfg-queue entry, using the passed
    config element and config factory if necessary.

    Returns a pair containing a potentially changed cfg-element and a boolean indicating whether
    the deletion was successful.
    '''

    delete_func: typing.Callable[[model.NamedModelElement, str, cmm.CfgQueueEntry], None] = None

    if (type_name := cfg_queue_entry.target.type) == 'container_registry':
        if cfg_element.registry_type() == om.OciRegistryType.GCR:
            delete_func = cmg.delete_config_secret
        else:
            f'{cfg_element.registry_type()} is not (yet) supported for automated deletion'
            return False

    elif type_name == 'gcp':
        delete_func = cmg.delete_config_secret

    elif type_name == 'github':
        delete_func = cmgh.delete_config_secret

    elif type_name == 'azure_service_principal':
        delete_func = cma.delete_config_secret

    elif type_name == 'btp_service_binding':
        delete_func = cmb.delete_config_secret

    elif type_name == 'btp_application_certificate':
        delete_func = cmbac.delete_config_secret

    elif type_name == 'aws':
        delete_func = cmaws.delete_config_secret

    elif type_name == 'alicloud':
        delete_func = cmali.delete_config_secret

    elif type_name == 'kubernetes':
        try:
            cmk.validate_for_rotation(cfg_element)
        # This can only happen if the kubernetes config was edited after the rotation
        # but before the removal of the expired secret
        except cmm.ValidationError as e:
            logger.warning(
                f"Cannot rotate cfg-type '{type_name}' with name '{cfg_element.name()}': {e}"
            )
            return None, None

        delete_func = cmk.delete_config_secret

    if not delete_func:
        logger.warning(
            f'{type_name} is not (yet) supported for automated deletion'
        )
        return None, False

    try:
        updated_cfg_element = delete_func(
            cfg_element=cfg_element,
            cfg_factory=cfg_factory,
            cfg_queue_entry=cfg_queue_entry,
        )
    except Exception as e:
        logger.error(
            f"error deleting secret for cfg-type '{type_name}' with name "
            f"'{cfg_element.name()}': {e}."
        )
        raise

    return updated_cfg_element, True


def rotate_cfg_element(
    cfg_element: model.NamedModelElement,
    cfg_factory: model.ConfigFactory,
) -> typing.Union[typing.Tuple[cfg_mgmt.revert_function, dict, model.NamedModelElement], None]:
    '''Rotates the credentials contained in the given config-element, using only config from the
    given factory (if necessary)

    Returns a triple of (callable, dict, NamedModelElement) if rotation for the given config-
    element is supported:
    - The callable is a function that reverts the rotation again.
    - The dict contains the meta-information about the rotation.
    - The returned NamedModelElement is the updated version of the element that was passed in.

    If rotation for the given element is not supported, `None` will be returned.
    '''
    type_name = cfg_element._type_name

    update_secret_function: typing.Callable[
        [model.NamedModelElement, model.ConfigFactory],
        typing.Tuple[cfg_mgmt.revert_function, dict, model.NamedModelElement]
    ] = None
    rotation_validation_function: typing.Callable[[model.NamedModelElement], None] = None

    if type_name == 'container_registry':
        if cfg_element.registry_type() == om.OciRegistryType.GCR:
            # service accounts require role "Service Account Key Admin"
            logger.info(f'rotating {cfg_element.name()} {type_name=}')
            update_secret_function = cmg.rotate_cfg_element
        else:
            logger.warning(
                f'{cfg_element.registry_type()} is not (yet) supported for automated rotation'
            )
            return None

    elif type_name == 'gcp':
        update_secret_function = cmg.rotate_cfg_element

    elif type_name == 'github':
        update_secret_function = cmgh.rotate_cfg_element

    elif type_name == 'azure_service_principal':
        update_secret_function = cma.rotate_cfg_element

    elif type_name == 'btp_service_binding':
        update_secret_function = cmb.rotate_cfg_element

    elif type_name == 'btp_application_certificate':
        update_secret_function = cmbac.rotate_cfg_element

    elif type_name == 'aws':
        rotation_validation_function = cmaws.validate_for_rotation
        update_secret_function = cmaws.rotate_cfg_element

    elif type_name == 'alicloud':
        rotation_validation_function = cmali.validate_for_rotation
        update_secret_function = cmali.rotate_cfg_element

    elif type_name == 'kubernetes':
        rotation_validation_function = cmk.validate_for_rotation
        update_secret_function = cmk.rotate_cfg_element

    if not update_secret_function:
        logger.warning(f'{type_name=} is not (yet) supported for automated rotation')
        return None

    try:
        if rotation_validation_function:
            rotation_validation_function(cfg_element)

        return update_secret_function(
            cfg_element=cfg_element,
            cfg_factory=cfg_factory,
        )

    except cmm.ValidationError as e:
        logger.warning(
            f"Cannot rotate cfg-type '{type_name}' with name '{cfg_element.name()}': {e}"
        )
        return None

    except Exception as e:
        logger.warning(f'an error occured whilst trying to update secret for {cfg_element=}: {e}')
        raise
