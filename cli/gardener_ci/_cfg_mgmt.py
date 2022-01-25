import cfg_mgmt.reporting as cmr
import cfg_mgmt.util as cmu

__cmd_name__ = 'cfg_mgmt'


def report(cfg_dir: str):
    status_reports = cmu.generate_cfg_element_status_reports(cfg_dir)

    cmr.create_report(
        cfg_element_statuses=status_reports,
    )
