import base64
import logging
import typing
import pprint
import re

import cfg_mgmt.model as cmm
import cfg_mgmt.util as cmu
import ccc.github
import ccc.secrets_server
import ci.log
import ci.util
import kube.ctx
import model
import model.concourse

ci.log.configure_default_logging()
logger = logging.getLogger(__name__)


def process_config_queue(
    cfg_dir: str,
    repo_url: str,
    github_repo_path: str,
    target_ref: str,
):
    '''
    Find first config queue entry that should be deleted and delete it.
    '''
    cfg_metadata = cmm.cfg_metadata_from_cfg_dir(cfg_dir=cfg_dir)
    cfg_factory = model.ConfigFactory.from_cfg_dir(
        cfg_dir=cfg_dir,
        disable_cfg_element_lookup=True,
    )
    github_cfg = ccc.github.github_cfg_for_repo_url(
        repo_url,
        cfg_factory=cfg_factory,
    )

    for cfg_queue_entry in cmu.iter_cfg_queue_entries_to_be_deleted(
        cfg_metadata=cfg_metadata,
    ):
        cfg_element = cfg_factory._cfg_element(
            cfg_type_name=cfg_queue_entry.target.type,
            cfg_name=cfg_queue_entry.target.name,
        )
        if cmu.process_cfg_queue_and_persist_in_repo(
            cfg_element=cfg_element,
            cfg_factory=cfg_factory,
            cfg_metadata=cfg_metadata,
            cfg_queue_entry=cfg_queue_entry,
            cfg_dir=cfg_dir,
            github_cfg=github_cfg,
            github_repo_path=github_repo_path,
            target_ref=target_ref,
        ):
            # stop after first successful deletion (avoid causing too much trouble at one time
            return
    logger.info('did not find a config queue entry to delete')


def rotate_secrets(
    cfg_dir: str,
    target_ref: str,
    repo_url: str,
    github_repo_path: str,
):
    cfg_metadata = cmm.cfg_metadata_from_cfg_dir(cfg_dir=cfg_dir)
    cfg_factory = model.ConfigFactory.from_cfg_dir(
        cfg_dir=cfg_dir,
        disable_cfg_element_lookup=True,
    )
    github_cfg = ccc.github.github_cfg_for_repo_url(
        repo_url,
        cfg_factory=cfg_factory,
    )

    for cfg_element in cmu.iter_cfg_elements_requiring_rotation(
        cmu.iter_cfg_elements(cfg_factory=cfg_factory),
        cfg_metadata=cfg_metadata,
        rotation_method=cmm.RotationMethod.AUTOMATED,
    ):
        if cmu.rotate_config_element_and_persist_in_cfg_repo(
            cfg_element=cfg_element,
            cfg_factory=cfg_factory,
            cfg_metadata=cfg_metadata,
            cfg_dir=cfg_dir,
            github_cfg=github_cfg,
            github_repo_path=github_repo_path,
            target_ref=target_ref,
        ):
            # stop after first successful rotation (avoid causing too much trouble at one time)
            break


def replicate_secrets(
    cfg_factory: model.ConfigFactory,
    cfg_set: model.ConfigurationSet,
    kubeconfig: typing.Dict,
    secret_key: str,
    secret_cipher_algorithm: str,
    future_secrets: typing.Dict[str, str],
    team_name: str,
    target_secret_name: str,
    target_secret_namespace: str,
    target_secret_cfg_name: str,
):
    logger.info(f'replicating replication cfg set {cfg_set.name()}')

    # force cfg_set serialiser to include referenced cfg_sets
    cfg_sets = list(cfg_set._cfg_elements('cfg_set')) + [cfg_set]

    for cfg_set in cfg_sets:
        logger.info(f'config subset {cfg_set.name()=} with keys')
        for cfg_mapping in [cfg_set._cfg_mappings()]:
            for cfg_type_name, _ in cfg_mapping:
                pprint.pprint(
                    {cfg_type_name: cfg_set._cfg_element_names(cfg_type_name=cfg_type_name)}
                )

    serialiser = model.ConfigSetSerialiser(cfg_sets=cfg_sets, cfg_factory=cfg_factory)

    kube_ctx = kube.ctx.Ctx(kubeconfig_dict=kubeconfig)
    secrets_helper = kube_ctx.secret_helper()

    logger.info(f'deploying indexed secrets on cluster {kube_ctx.kubeconfig.host}')
    for (k,v) in future_secrets.items():
        m = re.match(r'key[-](\d+)', k)
        if m:
            f_name = model.concourse.secret_name_from_team(team_name, m.group(1))

            encrypted_cipher_data = ccc.secrets_server.encrypt_data(
                key=v.encode('utf-8'),
                cipher_algorithm=secret_cipher_algorithm,
                serialized_secret_data=serialiser.serialise().encode('utf-8')
            )
            encoded_cipher_data = base64.b64encode(encrypted_cipher_data).decode('utf-8')
            logger.info(f'deploying secret {f_name} in namespace {target_secret_namespace}')
            secrets_helper.put_secret(
                name=f_name,
                raw_data={target_secret_cfg_name: encoded_cipher_data},
                namespace=target_secret_namespace,
            )
        else:
            logger.warning(f'ignoring unmatched key: {k}')

    logger.info(f'deployed encrypted secret for team: {team_name}')
