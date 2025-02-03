import functools

import google.oauth2.service_account as service_account
import googleapiclient.discovery
import google.cloud.storage

import ci.util


def _to_gcp_cfg(gcp_cfg: str):
    if isinstance(gcp_cfg, str):
        cfg_factory = ci.util.ctx().cfg_factory()
        gcp_cfg = cfg_factory.gcp(gcp_cfg)
    return gcp_cfg


def credentials(gcp_cfg: str):
    gcp_cfg = _to_gcp_cfg(gcp_cfg=gcp_cfg)

    credentials = service_account.Credentials.from_service_account_info(
        gcp_cfg.service_account_key(),
    )

    return credentials


def authenticated_build_func(gcp_cfg: str):
    creds = credentials(gcp_cfg=gcp_cfg)

    return functools.partial(googleapiclient.discovery.build, credentials=creds)


def cloud_storage_client(gcp_cfg: str, *args, **kwargs):
    gcp_cfg = _to_gcp_cfg(gcp_cfg=gcp_cfg)
    creds = credentials(gcp_cfg=gcp_cfg)

    return google.cloud.storage.Client(
        project=gcp_cfg.project(),
        credentials=creds,
        *args,
        **kwargs,
    )
