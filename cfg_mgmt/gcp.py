import base64
import dataclasses
import datetime
import json
import logging
import os
import typing
import yaml

import googleapiclient

import ccc.gcp
import ccc.github
import cfg_mgmt.model as cmm
import cfg_mgmt.util as cmu
import ci.log
import ci.util
import gitutil
import model
import model.container_registry


ci.log.configure_default_logging()
logger = logging.getLogger(__name__)


def create_service_account_key(
    iam_client: googleapiclient.discovery.Resource,
    service_account_name: str,
    body: dict = {},
) -> dict:
    '''
    Creates a key for a service account.
    '''

    key_request = iam_client.projects().serviceAccounts().keys().create(
        name=service_account_name,
        body=body,
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


def find_gcr_cfg_element_to_rotate(
    cfg_dir,
    cfg_fac,
    cfg_element_name,
) -> typing.Optional[model.container_registry.ContainerRegistryConfig]:
    for element in cmu.iter_cfg_elements_requiring_rotation(
        cfg_elements=cmu.iter_cfg_elements(
            cfg_factory=cfg_fac,
            cfg_target=cmm.CfgTarget(
                name=cfg_element_name,
                type='container_registry',
            ),
        ),
        cfg_metadata=cmm.cfg_metadata_from_cfg_dir(cfg_dir=cfg_dir),
        element_filter=lambda e: e.registry_type() == 'gcr',
    ):
        return element

    return None


def rotate_gcr_cfg_element(
    cfg_dir: str,
    target_ref: str,
    cfg_element: model.container_registry.ContainerRegistryConfig,
    github_api: gitutil.GitHelper,
):
    client_email = json.loads(cfg_element.password())['client_email']

    iam_client = ccc.gcp.create_iam_client(
        cfg_element=cfg_element,
    )

    service_account_name = ccc.gcp.qualified_service_account_name(
        client_email,
    )

    old_key_id = json.loads(cfg_element.password())['private_key_id']
    new_key = create_service_account_key(
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

    cfg_queue = ci.util.parse_yaml_file(os.path.join(cfg_dir, cmm.cfg_queue_fname))

    # add old key to rotation queue
    cfg_queue_entry = cmm.CfgQueueEntry(
        target=cmm.CfgTarget(
            name=cfg_element.name(),
            type='container_registry',
        ),
        deleteAfter=(datetime.datetime.now() + datetime.timedelta(days=7)).isoformat(),
        secretId={'gcp_secret_key': old_key_id},
    )
    cfg_queue['rotation-queue'].append(
        dataclasses.asdict(cfg_queue_entry),
    )

    with open(os.path.join(cfg_dir, cmm.cfg_queue_fname), 'w') as f:
        yaml.dump(
            cfg_queue,
            f,
        )
    logger.info('old key added to rotation queue')

    # update config status
    logger.info('updating config status')
    cfg_statuses = cmm.cfg_status(
        ci.util.parse_yaml_file(os.path.join(cfg_dir, cmm.cfg_status_fname))
    )

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

    commit = github_api.index_to_commit(
        message=f'[ci] rotate GCR credentials "{cfg_element.name()}"',
    )
    logger.info('local commit created')
    try:
        logger.info('pushing to remote')
        github_api.push(
            from_ref=commit.hexsha,
            to_ref=target_ref,
        )
        logger.info('secret rotated successfully')
    except:
        # push failed, delete newly created key
        logger.error('unable to push, deleting new key')
        delete_service_account_key(
            iam_client=iam_client,
            service_account_key_name=ccc.gcp.qualified_service_account_key_name(
                service_account_name=client_email,
                key_name=new_key['private_key_id'],
            )
        )


def rotate_cfg_element_if_rotation_required(
    cfg_element_name: str,
    cfg_dir: str,
    target_ref: str,
    repo_url: str,
    github_repo_path: str,
):
    cfg_fac = model.ConfigFactory.from_cfg_dir(
        cfg_dir=cfg_dir,
        disable_cfg_element_lookup=True,
    )
    if not (cfg_element := find_gcr_cfg_element_to_rotate(
        cfg_dir=cfg_dir,
        cfg_fac=cfg_fac,
        cfg_element_name=cfg_element_name,
        )
    ):
        logger.info('no cfg_element eligble for rotation found')
        return

    logger.info(f'rotating {cfg_element.name()}')

    github_cfg = ccc.github.github_cfg_for_repo_url(
        repo_url=repo_url,
    )
    github_api = gitutil.GitHelper(
        repo=cfg_dir,
        github_cfg=github_cfg,
        github_repo_path=github_repo_path,
    )
    rotate_gcr_cfg_element(
        cfg_element=cfg_element,
        cfg_dir=cfg_dir,
        github_api=github_api,
        target_ref=target_ref,
    )
