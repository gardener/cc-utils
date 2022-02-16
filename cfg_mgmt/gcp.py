import base64
import dataclasses
import datetime
import json
import logging
import os
import yaml

import googleapiclient

import ccc.gcp
import ccc.github
import cfg_mgmt.model as cmm
import ci.log
import ci.util
import gitutil
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
    target_ref: str,
    cfg_metadata: cmm.CfgMetadata,
):
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
    cfg_queue_entry = cmm.CfgQueueEntry(
        target=cmm.CfgTarget(
            name=cfg_element.name(),
            type='container_registry',
        ),
        deleteAfter=(datetime.datetime.now() + datetime.timedelta(days=7)).isoformat(),
        secretId={'gcp_secret_key': old_key_id},
    )

    cfg_metadata.queue.append(cfg_queue_entry),

    with open(os.path.join(cfg_dir, cmm.cfg_queue_fname), 'w') as f:
        yaml.dump(
            {
                'rotation_queue': [
                    dataclasses.asdict(cfg_queue_entry)
                    for cfg_queue_entry in cfg_metadata.queue
                ]
            },
            f,
        )
    logger.info('old key added to rotation queue')

    cfg_statuses = cfg_metadata.statuses

    # update credential timestamp, create if not present
    for cfg_status in cfg_statuses:
        if cfg_status.matches(
            element=cfg_element,
        ):
            break
    else:
        # does not exist
        cfg_status = cmm.CfgStatus(
            target=cmm.CfgTarget(
                type='container_registry',
                name=cfg_element.name(),
            ),
            credential_update_timestamp=datetime.datetime.now().isoformat(),
        )
        cfg_statuses.append(cfg_status)
    cfg_status.credential_update_timestamp = datetime.datetime.now().isoformat()

    with open(os.path.join(cfg_dir, cmm.cfg_status_fname), 'w') as f:
        yaml.dump(
            {
                'config_status': [
                    dataclasses.asdict(cfg_status)
                    for cfg_status in cfg_statuses
                ]
            },
            f,
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


def force_rotate_cfg_element(
    cfg_element_name: str,
    cfg_dir: str,
    repo_url: str,
    github_repo_path: str,
    target_ref: str,
):
    '''
    Rotates given cfg_element without checking whether rotation is required.
    '''
    cfg_fac = model.ConfigFactory.from_cfg_dir(
        cfg_dir=cfg_dir,
        disable_cfg_element_lookup=True,
    )
    cfg_element = cfg_fac.container_registry(cfg_element_name)

    logger.info('force rotating, rotation required is ignored')
    logger.info(f'rotating {cfg_element.name()}')

    github_cfg = ccc.github.github_cfg_for_repo_url(
        repo_url=repo_url,
    )
    git_helper = gitutil.GitHelper(
        repo=cfg_dir,
        github_cfg=github_cfg,
        github_repo_path=github_repo_path,
    )

    cfg_metadata = cmm.cfg_metadata_from_cfg_dir(cfg_dir)

    rotate_gcr_cfg_element(
        cfg_factory=cfg_fac,
        cfg_element=cfg_element,
        cfg_dir=cfg_dir,
        git_helper=git_helper,
        target_ref=target_ref,
        cfg_metadata=cfg_metadata,
    )
