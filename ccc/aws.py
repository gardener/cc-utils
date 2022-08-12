import logging
import typing

import boto3

import ci.util
import ctx
import model.aws

logger = logging.getLogger(__name__)


def session(aws_cfg: typing.Union[str, model.aws.AwsProfile], region_name=None):
    if isinstance(aws_cfg, str):
        cfg_factory = ci.util.ctx().cfg_factory()
        aws_cfg = cfg_factory.aws(aws_cfg)

    region_name = region_name or aws_cfg.region()

    session = boto3.Session(
        aws_access_key_id=aws_cfg.access_key_id(),
        aws_secret_access_key=aws_cfg.secret_access_key(),
        region_name=region_name,
    )

    return session


def default_session(
    cfg_factory=None,
    cfg_set=None,
):
    if not cfg_set and not cfg_factory:
        cfg_factory = ctx.cfg_factory()

    if not cfg_set:
        cfg_set = cfg_factory.cfg_set(ci.util.current_config_set_name())

    try:
        cfg = cfg_set.aws()
        return session(cfg)
    except:
        logger.warning('failed to retrieve default aws cfg')
        return None
