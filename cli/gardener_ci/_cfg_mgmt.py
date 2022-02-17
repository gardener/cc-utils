import logging
import re

import cfg_mgmt.gcp
import cfg_mgmt.reporting as cmr
import cfg_mgmt.rotate
import cfg_mgmt.model
import cfg_mgmt.util as cmu
import model

__cmd_name__ = 'cfg_mgmt'
logger = logging.getLogger(__name__)


def report(
    cfg_dir: str,
    responsible_names: [str]=[]
):
    status_reports = cmu.generate_cfg_element_status_reports(cfg_dir)

    def matches_any(name: str):
        for responsible_name in responsible_names:
            if re.fullmatch(responsible_name, name):
                return True
        return False

    def filtered_reports(responsible_names):
        print(responsible_names)
        if not responsible_names:
            yield from status_reports
            return

        for report in status_reports:
            if not (responsible_mapping := report.responsible):
                continue

            for responsible in responsible_mapping.responsibles:
                if matches_any(name=responsible.name):
                    break
            else:
                continue
            yield report

    reports = filtered_reports(responsible_names=responsible_names)

    for _ in cmr.create_report(cfg_element_statuses=reports):
        pass


def rotate(
    cfg_dir: str,
    type_name: str,
    name: str,
    github_cfg: str, # e.g. github_wdf_sap_corp
    cfg_repo_path: str, # e.g. kubernetes/cc-config
    tgt_ref: str='refs/heads/master',
):
    cfg_factory = model.ConfigFactory.from_cfg_dir(
        cfg_dir=cfg_dir,
        disable_cfg_element_lookup=True,
    )

    github_cfg = cfg_factory.github(github_cfg)

    cfg_element = cfg_factory._cfg_element(
        cfg_type_name=type_name,
        cfg_name=name,
    )

    cfg_metadata = cfg_mgmt.model.cfg_metadata_from_cfg_dir(cfg_dir=cfg_dir)

    cfg_mgmt.rotate.rotate_cfg_element(
        cfg_factory=cfg_factory,
        cfg_dir=cfg_dir,
        cfg_element=cfg_element,
        target_ref=tgt_ref,
        github_cfg=github_cfg,
        cfg_metadata=cfg_metadata,
        github_repo_path=cfg_repo_path,
    )
