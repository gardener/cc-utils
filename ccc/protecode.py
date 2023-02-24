import ci.util
import model.base
import model.protecode
import protecode.client


def client(
    protecode_cfg: model.protecode.ProtecodeConfig | str=None,
    group_id: int=None,
    base_url: str=None,
    cfg_factory=None,
) -> protecode.client.ProtecodeApi:
    '''
    convenience method to create a `ProtecodeApi`

    Either pass protecode_cfg directly (or reference by name), or
    lookup protecode_cfg based on group_id and base_url, most specific cfg wins.
    '''

    if not cfg_factory:
        cfg_factory = ci.util.ctx().cfg_factory()

    if group_id:
        group_id = int(group_id)
    if base_url:
        base_url = str(base_url)

    if protecode_cfg:
        if isinstance(protecode_cfg, str):
            protecode_cfg = cfg_factory.protecode(protecode_cfg)

        if protecode_cfg.matches(
            group_id=group_id,
            base_url=base_url,
        ) == -1:
            raise ValueError(protecode_cfg)

    else:
        if not (protecode_cfg := model.protecode.find_config(
            group_id=group_id,
            base_url=base_url,
            config_candidates=cfg_factory._cfg_elements(cfg_type_name='protecode'),
        )):
            raise model.base.ConfigElementNotFoundError(
                f'No protecode cfg found for {group_id=}, {base_url=}'
            )

    routes = protecode.client.ProtecodeApiRoutes(base_url=protecode_cfg.api_url())
    api = protecode.client.ProtecodeApi(
        api_routes=routes,
        protecode_cfg=protecode_cfg,
    )
    return api
