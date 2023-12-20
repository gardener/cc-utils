import copy
import dataclasses
import datetime
import logging
import typing

import boto3
import dacite

import cfg_mgmt
import ci.log
import model
import model.aws
import model.base

from cfg_mgmt.model import (
    CfgQueueEntry,
    ValidationError,
)


logger = logging.getLogger(__name__)
ci.log.configure_default_logging()


@dataclasses.dataclass
class AwsAccessKey:
    UserName: str
    AccessKeyId: str
    Status: str
    SecretAccessKey: str
    CreateDate: datetime.datetime


@dataclasses.dataclass
class AccessKeyMetadata:
    UserName: str
    AccessKeyId: str
    Status: str
    CreateDate: datetime.datetime


@dataclasses.dataclass
class CreateAccessKeyResponse:
    AccessKey: AwsAccessKey


@dataclasses.dataclass
class ListAccessKeysResponse:
    AccessKeyMetadata: typing.List[AccessKeyMetadata]
    IsTruncated: bool
    Marker: typing.Optional[str] # only present when IsTruncated is True


def rotation_cfg_or_none(
    aws_cfg: model.aws.AwsProfile,
    cfg_factory: model.ConfigFactory,
):
    if (rotation_cfg_reference := aws_cfg.rotation_cfg()):
        return cfg_factory.resolve_cfg_element_reference(
            cfg_element_reference=rotation_cfg_reference,
        )

    return None


def rotate_cfg_element(
    cfg_element: model.aws.AwsProfile,
    cfg_factory: model.ConfigFactory,
) ->  typing.Tuple[cfg_mgmt.revert_function, dict, model.NamedModelElement]:
    rotation_cfg = rotation_cfg_or_none(
        aws_cfg=cfg_element,
        cfg_factory=cfg_factory,
    ) or cfg_element

    logger.info(f'using {rotation_cfg.name()=} for rotation')

    iam_user_name = cfg_element.iam_user_name()

    iam_client = boto3.client(
        'iam',
        aws_access_key_id=rotation_cfg.access_key_id(),
        aws_secret_access_key=rotation_cfg.secret_access_key(),
        region_name=rotation_cfg.region(),
    )

    if iam_user_name:
        data = iam_client.create_access_key(UserName=iam_user_name)

    else:
        data = iam_client.create_access_key()

    response: CreateAccessKeyResponse = dacite.from_dict(
        data_class=CreateAccessKeyResponse,
        data=data,
    )
    access_key = response.AccessKey

    raw_cfg = copy.deepcopy(cfg_element.raw)
    new_element = model.aws.AwsProfile(
        name=cfg_element.name(), raw_dict=raw_cfg, type_name=cfg_element._type_name
    )
    new_element.raw['access_key_id'] = access_key.AccessKeyId
    new_element.raw['secret_access_key'] = access_key.SecretAccessKey

    def revert_function():
        if iam_user_name:
            iam_client.delete_access_key(
                AccessKeyId=access_key.AccessKeyId,
                UserName=iam_user_name,
            )
        else:
            iam_client.delete_access_key(AccessKeyId=access_key.AccessKeyId)

    secret_id = {'accessKeyId': cfg_element.access_key_id()}

    return revert_function, secret_id, new_element


def delete_config_secret(
    cfg_element: model.aws.AwsProfile,
    cfg_factory: model.ConfigFactory,
    cfg_queue_entry: CfgQueueEntry,
) -> model.aws.AwsProfile | None:
    rotation_cfg = rotation_cfg_or_none(
        aws_cfg=cfg_element,
        cfg_factory=cfg_factory,
    ) or cfg_element

    logger.info(f'using {rotation_cfg.name()=} for deletion')

    iam_user_name = cfg_element.iam_user_name()

    iam_client = boto3.client(
        'iam',
        aws_access_key_id=rotation_cfg.access_key_id(),
        aws_secret_access_key=rotation_cfg.secret_access_key(),
        region_name=rotation_cfg.region(),
    )
    access_key_id = cfg_queue_entry.secretId['accessKeyId']

    if iam_user_name:
        iam_client.delete_access_key(
            UserName=iam_user_name,
            AccessKeyId=access_key_id,
        )

    else:
        iam_client.delete_access_key(AccessKeyId=access_key_id)

    return None


def validate_for_rotation(
    cfg_element: model.aws.AwsProfile,
):
    cfg_element.validate()

    access_key_id = cfg_element.access_key_id()
    iam_client = boto3.client(
        'iam',
        aws_access_key_id=access_key_id,
        aws_secret_access_key=cfg_element.secret_access_key(),
        region_name=cfg_element.region(),
    )
    response: ListAccessKeysResponse = dacite.from_dict(
        data_class=ListAccessKeysResponse,
        data=iam_client.list_access_keys(),
    )
    key_metadata = response.AccessKeyMetadata

    if len(key_metadata) == 2:
        raise ValidationError(
            'There are already two keys present in AWS for Access Key '
            f"'{access_key_id}'. Will not attempt rotation."
        )
