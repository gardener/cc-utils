import model.protecode
import protecode.client


def client(
    protecode_cfg: model.protecode.ProtecodeConfig | str,
    cfg_factory=None,
):
    if isinstance(protecode_cfg, str):
        import ci.util
        if not cfg_factory:
            cfg_factory = ci.util.ctx().cfg_factory()
        protecode_cfg = cfg_factory.protecode(protecode_cfg)

    routes = protecode.client.ProtecodeApiRoutes(base_url=protecode_cfg.api_url())
    api = protecode.client.ProtecodeApi(
        api_routes=routes,
        protecode_cfg=protecode_cfg,
    )
    return api
