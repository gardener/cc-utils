import base64
import json
import logging
import os
import yaml

import googleapiclient

import ccc.gcp
import ccc.github
import cfg_mgmt
import cfg_mgmt.model as cmm
import cfg_mgmt.util as cmu
import ci.log
import ci.util
import model
import model.container_registry


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


def create_secret_and_persist_in_cfg_repo(
    cfg_dir: str,
    cfg_element: model.container_registry.ContainerRegistryConfig,
    cfg_metadata: cmm.CfgMetadata,
) ->  cfg_mgmt.revert_function:
    client_email = json.loads(cfg_element.password())['client_email']

    iam_client = ccc.gcp.create_iam_client(
        cfg_element=cfg_element,
    )

    service_account_name = ccc.gcp.qualified_service_account_name(
        client_email,
    )

    old_key_id = json.loads(cfg_element.password())['private_key_id']
    old_key_id = ccc.gcp.qualified_service_account_key_name(
        service_account_name=client_email,
        key_name=old_key_id,
    )

    new_key = _create_service_account_key(
        iam_client=iam_client,
        service_account_name=service_account_name,
    )

    cfg_file = ci.util.parse_yaml_file(os.path.join(cfg_dir, cmm.container_registry_fname))

    # patch secret
    cfg_file[cfg_element.name()]['password'] = json.dumps(new_key)
    with open(os.path.join(cfg_dir, cmm.container_registry_fname), 'w') as f:
        yaml.safe_dump(
            cfg_file,
            f,
        )
    logger.info('secret patched')

    # add old key to rotation queue
    cfg_metadata.queue.append(
        cmu.create_config_queue_entry(
            queue_entry_config_element=cfg_element,
            queue_entry_data={'gcp_secret_key': old_key_id},
        )
    )

    cmu.write_config_queue(
        cfg_dir=cfg_dir,
        cfg_metadata=cfg_metadata,
    )
    logger.info('old key added to rotation queue')

    # update credential timestamp, create if not present
    cmu.update_config_status(
        config_element=cfg_element,
        config_statuses=cfg_metadata.statuses,
        cfg_status_file_path=os.path.join(
            cfg_dir,
            cmm.cfg_status_fname,
        )
    )

    def revert():
        delete_service_account_key(
            iam_client=iam_client,
            service_account_key_name=ccc.gcp.qualified_service_account_key_name(
                service_account_name=client_email,
                key_name=new_key['private_key_id'],
            )
        )

    return revert
