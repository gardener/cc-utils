import os

import cfg_mgmt.model as cmm
import cfg_mgmt.reporting as cmr
import cfg_mgmt.util as cmu
import ci.util
import model

__cmd_name__ = 'cfg_mgmt'


def report(cfg_dir: str):
    ci.util.existing_dir(cfg_dir)

    cfg_factory = model.ConfigFactory._from_cfg_dir(
        cfg_dir,
        disable_cfg_element_lookup=True,
    )

    policies = cmm.cfg_policies(
        policies=cmm._parse_cfg_policies_file(
            path=os.path.join(cfg_dir, cmm.cfg_policies_fname),
        )
    )
    rules = cmm.cfg_rules(
        rules=cmm._parse_cfg_policies_file(
            path=os.path.join(cfg_dir, cmm.cfg_policies_fname),
        )
    )
    statuses = cmm.cfg_status(
        status=cmm._parse_cfg_status_file(
            path=os.path.join(cfg_dir, cmm.cfg_status_fname),
        )
    )
    responsibles = cmm.cfg_responsibles(
        responsibles=cmm._parse_cfg_responsibles_file(
            path=os.path.join(cfg_dir, cmm.cfg_responsibles_fname),
        )
    )

    statuses = [
        cmu.determine_status(
            element=element,
            policies=policies,
            rules=rules,
            statuses=statuses,
            responsibles=responsibles,
            element_storage=cfg_dir,
        ) for element in cmu.iter_cfg_elements(cfg_factory=cfg_factory)
    ]

    cmr.create_report(
        cfg_element_statuses=statuses,
    )
