import base64
import logging
import os
import typing

import ccc.secrets_server
import ci.log
import ci.util
import model
import model.concourse
import kube.ctx


ci.log.configure_default_logging()
logger = logging.getLogger(__name__)


def replicate_secrets(
    cfg_dir_env_name: str,
    kubeconfig: typing.Dict,
    secrets_cfg_name: str,
    team_name: str,
    target_secret_name: str,
    target_secret_namespace: str,
):
    cfg_factory: model.ConfigFactory = model.ConfigFactory.from_cfg_dir(
        cfg_dir=os.environ.get(cfg_dir_env_name),
    )
    secret = cfg_factory.secret(secrets_cfg_name)

    kube_ctx = kube.ctx.Ctx(kubeconfig_dict=kubeconfig)
    logger.info(f'deploying secret on cluster {kube_ctx.kubeconfig.host}')
    secrets_helper = kube_ctx.secret_helper()
    encrypted_cipher_data = ccc.secrets_server.encrypt_data(
        secret=secret,
        serialized_secret_data=cfg_factory._serialise().encode('utf-8')
    )

    encoded_cipher_data = base64.b64encode(encrypted_cipher_data).decode('utf-8')

    logger.info(f'deploying encrypted secret for team: {team_name}')
    # FIXME remove hardcoded name
    secrets_helper.put_secret(
        name=target_secret_name,
        raw_data={f'{team_name}_cfg': encoded_cipher_data},
        namespace=target_secret_namespace,
    )
