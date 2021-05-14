import logging
import os
import typing

import kube.ctx
import ci.log
import ci.util
import model
import model.concourse


ci.log.configure_default_logging()
logger = logging.getLogger(__name__)


def replicate_secrets(
    cfg_dir_env_name: str,
    kubeconfig: typing.Dict,
    job_mapping: model.concourse.JobMapping,
):
    secret_cfg_name = job_mapping.secret_cfg()
    logger.info(f'{cfg_dir_env_name=} {secret_cfg_name=}')

    cfg_dir_path = os.environ.get(cfg_dir_env_name)

    cfg_factory: model.ConfigFactory = model.ConfigFactory.from_cfg_dir(cfg_dir=cfg_dir_path)
    config_data = cfg_factory.serialise()

    secret = cfg_factory.secret()

    kube_ctx = kube.ctx.Ctx(kubeconfig_dict=kubeconfig)
    print(kube_ctx.kubeconfig.host)
