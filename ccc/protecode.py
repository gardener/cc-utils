import protecode.client
import model.protecode


def client(
    protecode_cfg: model.protecode.ProtecodeConfig,
    parallel_jobs=12,
):
    if isinstance(protecode_cfg, str):
        import ci.util
        cfg_factory = ci.util.ctx().cfg_factory()
        protecode_cfg = cfg_factory.protecode(protecode_cfg)

    routes = protecode.client.ProtecodeApiRoutes(base_url=protecode_cfg.api_url())
    api = protecode.client.ProtecodeApi(
        api_routes=routes,
        basic_credentials=protecode_cfg.credentials(),
        tls_verify=protecode_cfg.tls_verify(),
    )
    return api
