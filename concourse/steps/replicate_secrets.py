import base64
import logging

import kubernetes.client
import kubernetes.config

import ccc.delivery
import ccc.github
import ccc.secrets_server
import cfg_mgmt.model as cmm
import cfg_mgmt.reporting as cmr
import cfg_mgmt.util as cmu
import ci.log
import ci.util
import model
import model.concourse
import model.config_repo
import model.secret
import model.secrets_server

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

    for cfg_element in cmr.iter_cfg_elements_requiring_rotation(
        cmu.iter_cfg_elements(cfg_factory=cfg_factory),
        cfg_metadata=cfg_metadata,
        rotation_method=cmm.RotationMethod.AUTOMATED,
    ):
        logger.info(
            f"Rotating config-element '{cfg_element.name()}' of type '{cfg_element._type_name}'"
        )
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


def _put_secret(
        core_api: kubernetes.client.CoreV1Api,
        name: str,
        data: dict = None,
        namespace: str='default',
        raw_data: dict = None,
):
    '''creates or updates (replaces) the specified secret.
    the secret's contents are expected in a dictionary containing only scalar values.
    In particular, each value is converted into a str; the result returned from
    to-str conversion is encoded as a utf-8 byte array. Thus such a conversion must
    not have done before.
    '''
    if not bool(data) ^ bool(raw_data):
        raise ValueError('Exactly one data or raw data has to be set')

    metadata = kubernetes.client.V1ObjectMeta(
        name=ci.util.not_empty(name),
        namespace=ci.util.not_empty(namespace),
    )

    if data:
        raw_data = {
            k: base64.b64encode(str(v).encode('utf-8')).decode('utf-8')
            for k,v in data.items()
        }

    secret = kubernetes.client.V1Secret(metadata=metadata, data=raw_data)

    # find out whether we have to replace or to create
    try:
        core_api.read_namespaced_secret(name=name, namespace=namespace)
        secret_exists = True
    except kubernetes.client.ApiException as ae:
        # only 404 is expected
        if not ae.status == 404:
            raise ae
        secret_exists = False

    if secret_exists:
        core_api.replace_namespaced_secret(name=name, namespace=namespace, body=secret)
    else:
        core_api.create_namespaced_secret(namespace=namespace, body=secret)


def _put_k8s_secret(
    cfg_factory: model.ConfigFactory,
    target: model.config_repo.KubernetesSecretTarget,
    cfg_sets: list[model.ConfigurationSet],
):
    kubernetes_config = cfg_factory.kubernetes(target.kubernetes_config_name)
    target_kubeconfig = kubernetes_config.kubeconfig()

    secret_name = target.secret_name
    secret_namespace = target.secret_namespace
    secret_key = target.secret_key

    serialiser = model.ConfigSetSerialiser(cfg_sets=cfg_sets, cfg_factory=cfg_factory)
    serialised = serialiser.serialise().encode('utf-8')
    secret_data = base64.b64encode(serialised).decode('utf-8')

    api_client = kubernetes.config.new_client_from_config_dict(target_kubeconfig)
    core_api = kubernetes.client.CoreV1Api(api_client)

    logger.info(
        f"deploying config into k8s-secret '{secret_name}' in namespace "
        f"'{secret_namespace}' on cluster '{api_client.configuration.host}'"
    )
    _put_secret(
        core_api=core_api,
        name=secret_name,
        raw_data={secret_key: secret_data},
        namespace=secret_namespace,
    )


def _put_secrets_server_secret(
    cfg_factory: model.ConfigFactory,
    target: model.config_repo.SecretsServerTarget,
    cfg_sets: list[model.ConfigurationSet],
):
    kubernetes_config = cfg_factory.kubernetes(target.kubernetes_config_name)
    target_kubeconfig = kubernetes_config.kubeconfig()

    secret_config: model.secret.Secret = cfg_factory.secret(target.secret_config)
    secrets_server_config: model.secrets_server.SecretsServerConfig = \
        cfg_factory.secrets_server(target.secrets_server_config)
    logger.info(
        f'secret cfg: {secret_config.name()} '
        f'and key: {secret_config.key().decode("utf-8")[:3]}...'
    )

    team_cfg = cfg_factory.concourse_team_cfg(target.team_config)
    concourse_team_name = team_cfg.team_name()

    secret_name = model.concourse.secret_name_from_team(
        team_name=concourse_team_name,
        key_generation=secret_config.generation(),
    )
    secret_namespace = secrets_server_config.namespace()
    secret_key = model.concourse.secret_cfg_name_for_team(concourse_team_name)

    serialiser = model.ConfigSetSerialiser(cfg_sets=cfg_sets, cfg_factory=cfg_factory)
    encrypted_cipher_data = ccc.secrets_server.encrypt_data(
        key=secret_config.key(),
        cipher_algorithm=secret_config.cipher_algorithm(),
        serialized_secret_data=serialiser.serialise().encode('utf-8')
    )
    secret_data = base64.b64encode(encrypted_cipher_data).decode('utf-8')

    api_client = kubernetes.config.new_client_from_config_dict(target_kubeconfig)
    core_api = kubernetes.client.CoreV1Api(api_client)

    logger.info(
        f"deploying encrypted secret '{secret_name}' in namespace '{secret_namespace}' "
        f"on cluster '{api_client.configuration.host}'"
    )
    _put_secret(
        core_api=core_api,
        name=secret_name,
        raw_data={secret_key: secret_data},
        namespace=secret_namespace,
    )


def replicate_secrets(
    cfg_factory: model.ConfigFactory,
    replication_target_config: model.config_repo.ReplicationTargetConfig,
):
    for mapping in replication_target_config.replication_mappings:
        cfg_set = cfg_factory.cfg_set(mapping.cfg_set)
        # force cfg_set serialiser to include referenced cfg_sets
        cfg_sets = list(cfg_set._cfg_elements('cfg_set')) + [cfg_set]
        if isinstance(target := mapping.target, model.config_repo.KubernetesSecretTarget):
            _put_k8s_secret(
                cfg_factory=cfg_factory,
                target=target,
                cfg_sets=cfg_sets,
            )
        elif isinstance(target, model.config_repo.SecretsServerTarget):
            _put_secrets_server_secret(
                cfg_factory=cfg_factory,
                target=target,
                cfg_sets=cfg_sets,
            )
        else:
            raise NotImplementedError(type(mapping))
