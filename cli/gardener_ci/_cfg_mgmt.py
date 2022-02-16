import logging
import re

import cfg_mgmt.gcp
import cfg_mgmt.reporting as cmr
import cfg_mgmt.util as cmu

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


def rotate_gcr_config(
    cfg_dir: str,
    element_name: str,
    repo_url: str,
    github_repo_path: str,
    target_ref: str,
):
    '''
    Rotates GCR credential from given directory without checking whether rotation is required.
    A new secret-key is created, and stored in the configuration element store
    (container_registry.yaml), replacing the previous one. In addition, the update-timestamp
    for the configuration element is updated in config_status.yaml, and the old secret key is
    marked for deletion (config_queue.yaml). The change is commited, and pushed to the given
    push target. In case pushing fails, the secret key is deleted again.
    '''
    cfg_mgmt.gcp.force_rotate_cfg_element(
        cfg_element_name=element_name,
        cfg_dir=cfg_dir,
        repo_url=repo_url,
        github_repo_path=github_repo_path,
        target_ref=target_ref,
    )
