import ci.util
from dso.compliancedb.db import ComplianceDB


def default_with_cfg_name(
    cfg_name: str,
):
    cfg_fac = ci.util.ctx().cfg_factory()
    cfg = cfg_fac.compliancedb(cfg_name)
    return ComplianceDB(
        username=cfg.credentials().username(),
        password=cfg.credentials().password(),
        hostname=cfg.hostname(),
        port=cfg.port(),
    )
