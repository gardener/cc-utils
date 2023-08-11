import copy
import dataclasses
import datetime
import dateutil.parser
import json
import logging
import typing

import dacite
import aliyunsdkcore.client
from aliyunsdkram.request.v20150501.ListAccessKeysRequest import ListAccessKeysRequest
from aliyunsdkram.request.v20150501.CreateAccessKeyRequest import CreateAccessKeyRequest
from aliyunsdkram.request.v20150501.DeleteAccessKeyRequest import DeleteAccessKeyRequest
from aliyunsdkcore.acs_exception.exceptions import ServerException

import cfg_mgmt
import ci.log
import model
import model.aws
import model.alicloud

from cfg_mgmt.model import (
    CfgQueueEntry,
    ValidationError,
)


logger = logging.getLogger(__name__)
ci.log.configure_default_logging()


@dataclasses.dataclass
class AlicloudAccessKey:
    AccessKeyId: str
    Status: str
    AccessKeySecret: str
    CreateDate: datetime.datetime

    def __post_init__(self):
        self.CreateDate = dateutil.parser.isoparse(self.CreateDate)


@dataclasses.dataclass
class AccessKeyMetadata:
    AccessKeyId: str
    Status: str
    CreateDate: datetime.datetime

    def __post_init__(self):
        self.CreateDate = dateutil.parser.isoparse(self.CreateDate)


@dataclasses.dataclass
class CreateAccessKeyResponse:
    AccessKey: AlicloudAccessKey
    RequestId: str


@dataclasses.dataclass
class AccessKeysEnumeration:
    AccessKey: typing.List[AccessKeyMetadata]


@dataclasses.dataclass
class ListAccessKeysResponse:
    RequestId: str
    AccessKeys: AccessKeysEnumeration


def rotate_cfg_element(
    cfg_element: model.alicloud.AlicloudConfig,
    cfg_factory: model.ConfigFactory,
) ->  typing.Tuple[cfg_mgmt.revert_function, dict, model.NamedModelElement]:

    client = aliyunsdkcore.client.AcsClient(
        ak=cfg_element.access_key_id(),
        secret=cfg_element.access_key_secret(),
    )

    request = CreateAccessKeyRequest()
    try:
        response = client.do_action_with_exception(request)
    except ServerException as e:
        if e.get_http_status() == 403:
            logger.warning(
                'User is not allowed to create a new access key. Please make sure that the '
                'Account is configured to allow users to manage their own keys.'
            )
        raise

    response: CreateAccessKeyResponse = dacite.from_dict(
        data_class=CreateAccessKeyResponse,
        data=json.loads(response),
        config=dacite.Config(check_types=False),
    )
    access_key = response.AccessKey

    raw_cfg = copy.deepcopy(cfg_element.raw)
    new_element = model.alicloud.AlicloudConfig(
        name=cfg_element.name(), raw_dict=raw_cfg, type_name=cfg_element._type_name
    )
    new_element.raw['access_key_id'] = access_key.AccessKeyId
    new_element.raw['access_key_secret'] = access_key.AccessKeySecret

    def revert_function():
        request = DeleteAccessKeyRequest()
        request.set_UserAccessKeyId(access_key.AccessKeyId)
        client.do_action_with_exception(request)

    secret_id = {'accessKeyId': cfg_element.access_key_id()}

    return revert_function, secret_id, new_element


def delete_config_secret(
    cfg_element: model.alicloud.AlicloudConfig,
    cfg_factory: model.ConfigFactory,
    cfg_queue_entry: CfgQueueEntry,
) -> model.alicloud.AlicloudConfig | None:
    client = aliyunsdkcore.client.AcsClient(
        ak=cfg_element.access_key_id(),
        secret=cfg_element.access_key_secret(),
    )
    access_key_id = cfg_queue_entry.secretId['accessKeyId']

    request = DeleteAccessKeyRequest()
    request.set_UserAccessKeyId(access_key_id)

    try:
        client.do_action_with_exception(request)
    except ServerException as e:
        if e.get_http_status() == 403:
            logger.warning(
                'User is not allowed to delete its own access key. Please make sure that '
                'the Account is configured to allow users to manage their own keys.'
            )
        raise

    return None


def validate_for_rotation(
    cfg_element: model.alicloud.AlicloudConfig,
):
    access_key_id = cfg_element.access_key_id()
    client = aliyunsdkcore.client.AcsClient(
        ak=access_key_id,
        secret=cfg_element.access_key_secret(),
    )

    request = ListAccessKeysRequest()
    try:
        response = client.do_action_with_exception(request)
    except ServerException as e:
        if e.get_http_status() == 403:
            logger.warning(
                'User is not allowed to list its own access keys. Please make sure that the '
                'Account is configured to allow users to manage their own keys.'
            )
            raise

    response: ListAccessKeysResponse = dacite.from_dict(
        data_class=ListAccessKeysResponse,
        data=json.loads(response),
        config=dacite.Config(check_types=False),
    )

    access_keys = response.AccessKeys.AccessKey

    if len(access_keys) == 2:
        raise ValidationError(
            'There are already two keys present in Alicloud for Access Key '
            f"'{access_key_id}'. Will not attempt rotation."
        )
