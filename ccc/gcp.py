import functools

import google.oauth2.service_account as service_account
import googleapiclient.discovery

import ci.util


def credentials(gcp_cfg: str):
    if isinstance(gcp_cfg, str):
        cfg_factory = ci.util.ctx().cfg_factory()
        gcp_cfg = cfg_factory.gcp(gcp_cfg)

    credentials = service_account.Credentials.from_service_account_info(
        gcp_cfg.service_account_key(),
    )

    return credentials


def authenticated_build_func(gcp_cfg: str):
    creds = credentials(gcp_cfg=gcp_cfg)

    return functools.partial(googleapiclient.discovery.build, credentials=creds)
