import base64
import copy
import logging
import time
import typing

import kubernetes.client
import kubernetes.config

import cfg_mgmt
import cfg_mgmt.util
import ci.util
import model
import model.kubernetes

from cfg_mgmt.model import (
    CfgQueueEntry,
    ValidationError,
)
from kubernetes.client import (
    CoreV1Api,
    V1ObjectMeta,
    V1Secret,
    V1ServiceAccount,
)


ci.log.configure_default_logging()
logger = logging.getLogger(__name__)


def rotate_cfg_element(
    cfg_element: model.kubernetes.KubernetesConfig,
    cfg_factory: model.ConfigFactory,
) -> typing.Tuple[cfg_mgmt.revert_function, dict, model.NamedModelElement]:

    logger.warning(f"THIS is the new LOCAL method gardener_sa.rotate_cfg_element")

    # copy passed cfg_element, since we update in-place.
    raw_cfg = copy.deepcopy(cfg_element.raw)
    cfg_to_rotate = model.kubernetes.KubernetesConfig(
        name=cfg_element.name(), raw_dict=raw_cfg, type_name=cfg_element._type_name
    )
    logger.info(f"{cfg_element._type_name=}")

    logger.info(f"kubeconfig={cfg_element.kubeconfig()}")
    api_client = kubernetes.config.new_client_from_config_dict(cfg_element.kubeconfig())
    core_api = kubernetes.client.CoreV1Api(api_client)

    # create new token
    cfg_loader = kubernetes.config.kube_config.KubeConfigLoader(
        dict(cfg_element.kubeconfig())
    )
    user = cfg_loader.current_context["context"]["user"]
    namespace = cfg_loader.current_context["context"]["namespace"]
    logger.info(
        f"read from kubeconfig with cfg_loader.current_context(), {user=}, {namespace=}"
    )

    logger.info(f"Create new token {user=}, {namespace=}")
    new_access_token = core_api.create_namespaced_service_account_token(
        name=user,
        namespace=namespace,
        body={
            "spec": {"expirationSeconds": 7776000},
        },
    )
    logger.info(f"{new_access_token=}")

    if not new_access_token:
        raise RuntimeError(
            f"Error getting new kubeconfig for {cfg_to_rotate.name()=}: {user=}, {namespace=}."
        )

    if cfg_to_rotate.kubeconfig()["users"][0]["user"]["token"] == new_access_token.status.token:
        logger.warning('New and old token are the same')
    else:
        logger.info('Saving new token...')

    cfg_to_rotate.kubeconfig()["users"][0]["user"]["token"] = new_access_token.status.token

    # raise RuntimeError(
    #     f"Just end the script."
    # )

    def revert():
        logger.warning(f'An error occurred during update of kubernetes config {cfg_to_rotate.name()}.')

    # keep this empty as old configs don't need to be deleted.
    secret_id = None
    return revert, secret_id, cfg_to_rotate


def validate_for_rotation(
    cfg_element: model.kubernetes.KubernetesConfig,
):
    # TODO: check if validity is still ok, or let it be as on renew it will give an error
    if not cfg_element.kubeconfig():
        raise ValidationError(f'Cannot rotate {cfg_element.name()=} without kubeconfig.')
