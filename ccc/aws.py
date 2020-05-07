import typing

import boto3

import ci.util
import model.aws


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
