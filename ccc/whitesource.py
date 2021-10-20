import functools

import ci.util
import whitesource.client


@functools.lru_cache()
def make_client(
    whitesource_cfg_name: str,
) -> whitesource.client.WhitesourceClient:

    cfg_fac = ci.util.ctx().cfg_factory()
    ws_config = cfg_fac.whitesource(whitesource_cfg_name)

    return whitesource.client.WhitesourceClient(
        api_key=ws_config.api_key(),
        extension_endpoint=ws_config.extension_endpoint(),
        wss_api_endpoint=ws_config.wss_api_endpoint(),
        wss_endpoint=ws_config.wss_endpoint(),
        ws_creds=ws_config.credentials(),
        product_token=ws_config.product_token(),
        requester_mail=ws_config.requester_mail(),
    )
