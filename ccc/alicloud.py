import oss2
from aliyunsdkcore.client import AcsClient
import ci.util


def _credentials(alicloud_cfg: str):
    if isinstance(alicloud_cfg, str):
        cfg_factory = ci.util.ctx().cfg_factory()
        alicloud_cfg = cfg_factory.alicloud(alicloud_cfg)
    return alicloud_cfg


def oss_auth(alicloud_cfg: str):
    cred = _credentials(alicloud_cfg)
    return oss2.Auth(cred.access_key_id(), cred.access_key_secret())


def acs_client(alicloud_cfg: str):
    cred = _credentials(alicloud_cfg)

    return AcsClient(
        ak=cred.access_key_id(),
        secret=cred.access_key_secret(),
        region_id=cred.region()
    )
