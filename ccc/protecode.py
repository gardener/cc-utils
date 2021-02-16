import ci.util
import model.protecode
import protecode.client


def client(
    protecode_cfg: model.protecode.ProtecodeConfig,
):
    if isinstance(protecode_cfg, str):
        import ci.util
        cfg_factory = ci.util.ctx().cfg_factory()
        protecode_cfg = cfg_factory.protecode(protecode_cfg)

    routes = protecode.client.ProtecodeApiRoutes(base_url=protecode_cfg.api_url())
    api = protecode.client.ProtecodeApi(
        api_routes=routes,
        protecode_cfg=protecode_cfg,
    )
    return api


def client_from_config_name(protecode_cfg_name: str):
    cfg_factory = ci.util.ctx().cfg_factory()
    protecode_config = cfg_factory.protecode(protecode_cfg_name)
    return client(protecode_config)
