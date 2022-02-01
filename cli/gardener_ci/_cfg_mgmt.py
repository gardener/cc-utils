import logging
import re

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

    cmr.create_report(
        cfg_element_statuses=reports,
    )
