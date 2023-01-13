import base64
import copy
import json
import logging
import typing

import googleapiclient

import ccc.gcp
import ccc.github
import cfg_mgmt
import cfg_mgmt.model as cmm
import ci.log
import ci.util
import model
import model.container_registry
import model.gcp


ci.log.configure_default_logging()
logger = logging.getLogger(__name__)


def _create_service_account_key(
    iam_client: googleapiclient.discovery.Resource,
    service_account_name: str,
) -> dict:
    '''
    Creates a key for a service account.
    '''

    key_request = iam_client.projects().serviceAccounts().keys().create(
        name=service_account_name,
        body={},
    )
    try:
        key = key_request.execute()
    except googleapiclient.errors.HttpError as e:
        logger.error('unable to create key, probably too many (10) active keys?')
        raise e

    logger.info('Created key: ' + key['name'])
    return json.loads(base64.b64decode(key['privateKeyData']))


def delete_service_account_key(
    iam_client: googleapiclient.discovery.Resource,
    service_account_key_name: str,
):
    iam_client.projects().serviceAccounts().keys().delete(
        name=service_account_key_name,
    ).execute()
    logger.info('Deleted key: ' + service_account_key_name)


def rotate_cfg_element(
    cfg_element: model.container_registry.ContainerRegistryConfig | model.gcp.GcpServiceAccount,
    cfg_factory: model.ConfigFactory,
) ->  typing.Tuple[cfg_mgmt.revert_function, dict, model.NamedModelElement]:
    client_email = cfg_element.client_email()

    iam_client = ccc.gcp.create_iam_client(
        cfg_element=cfg_element,
    )

    service_account_name = ccc.gcp.qualified_service_account_name(
        client_email,
    )

    old_key_id = cfg_element.private_key_id()
    old_key_id = ccc.gcp.qualified_service_account_key_name(
        service_account_name=client_email,
        key_name=old_key_id,
    )

    new_key = _create_service_account_key(
        iam_client=iam_client,
        service_account_name=service_account_name,
    )

    raw_cfg = copy.deepcopy(cfg_element.raw)

    if isinstance(cfg_element, model.container_registry.ContainerRegistryConfig):
        raw_cfg['password'] = json.dumps(new_key)
    elif isinstance(cfg_element, model.gcp.GcpServiceAccount):
        raw_cfg['service_account_key'] = new_key
    else:
        raise ValueError(cfg_element)

    updated_elem = type(cfg_element)(
        # checked for correct type already
        name=cfg_element.name(),
        raw_dict=raw_cfg,
        type_name=cfg_element._type_name,
    )

    secret_id = {'gcp_secret_key': old_key_id}

    def revert():
        delete_service_account_key(
            iam_client=iam_client,
            service_account_key_name=ccc.gcp.qualified_service_account_key_name(
                service_account_name=client_email,
                key_name=new_key['private_key_id'],
            )
        )

    return revert, secret_id, updated_elem


def delete_config_secret(
    cfg_element: model.container_registry.ContainerRegistryConfig,
    cfg_queue_entry: cmm.CfgQueueEntry,
    cfg_factory: model.ConfigFactory,
):
    logger.info('deleting old gcr secret')
    iam_client = ccc.gcp.create_iam_client(
        cfg_element=cfg_element,
    )
    delete_service_account_key(
        iam_client=iam_client,
        service_account_key_name=cfg_queue_entry.secretId['gcp_secret_key'],
    )
