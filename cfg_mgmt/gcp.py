import base64
import dataclasses
import datetime
import git
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
import concourse.util
import gitutil
import model
import model.container_registry
import oci.model as om


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
        element_filter=lambda e: e.registry_type() == om.OciRegistryType.GCR,
        rotation_method=cmm.RotationMethod.AUTOMATED,
    ):
        return element

    return None


def rotate_gcr_cfg_element(
    cfg_factory,
    cfg_dir: str,
    cfg_element: model.container_registry.ContainerRegistryConfig,
    git_helper: gitutil.GitHelper,
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

    new_key = create_service_account_key(
        iam_client=iam_client,
        service_account_name=service_account_name,
    )

    try:
        _try_rotate_gcr_cfg_element(
            cfg_factory=cfg_factory,
            cfg_dir=cfg_dir,
            cfg_element=cfg_element,
            git_helper=git_helper,
            target_ref=target_ref,
            cfg_metadata=cfg_metadata,
            new_key=new_key,
            old_key_id=old_key_id,
        )
    except:
        logger.error('something went wrong')
        try:
            git_helper.repo.git.reset('--hard')
        finally:
            logger.info('deleting new key (again)')
            delete_service_account_key(
                iam_client=iam_client,
                service_account_key_name=ccc.gcp.qualified_service_account_key_name(
                    service_account_name=client_email,
                    key_name=new_key['private_key_id'],
                )
            )
        raise


def _try_rotate_gcr_cfg_element(
    cfg_factory,
    cfg_dir: str,
    cfg_element: model.container_registry.ContainerRegistryConfig,
    git_helper: gitutil.GitHelper,
    target_ref: str,
    cfg_metadata: cmm.CfgMetadata,
    new_key,
    old_key_id: str,
):
    '''
    Creates new GCR Service Account Key and patches config.
    Old Key is appended to rotation queue and config status is updated.
    A local commit is created and pushed.
    If pushing fails, the newly created key is removed and a checkout (HEAD~1)
    on the repo is performed.
    '''

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

    actor = git.Actor(
        git_helper.github_cfg.credentials().username(),
        git_helper.github_cfg.credentials().email_address(),
    )

    repo = git_helper.repo
    repo.git.add(os.path.abspath(cfg_dir))

    commit_msg = f'[ci] rotate credential {cfg_element._type_name}:{cfg_element.name()}'
    # TODO: Rm once multiple gcr key creation fixed
    if ci.util._running_on_ci():
        try:
            build_url = concourse.util.own_running_build_url(
                cfg_factory=cfg_factory,
            )
            commit_msg += f'{build_url=}'
        except:
            pass # do not fail just because we cannot find out build-url

    repo.index.commit(
        commit_msg,
        author=actor,
        committer=actor,
    )

    try:
        logger.info('pushing changed secret')
        git_helper.push(
            from_ref='@',
            to_ref=target_ref,
        )
        logger.info('secret rotated successfully')
    except:
        # undo local changes
        logger.error('unable to push')
        logger.info('make local repo consistent again')
        git_helper.repo.git.reset('--hard', '@~')
        raise


def rotate_cfg_element_if_rotation_required(
    cfg_element_name: str,
    cfg_dir: str,
    repo_url: str,
    github_repo_path: str,
    target_ref: str,
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
