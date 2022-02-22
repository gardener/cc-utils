import logging
import re

import cfg_mgmt.gcp
import cfg_mgmt.reporting as cmr
import cfg_mgmt.rotate
import cfg_mgmt.model
import cfg_mgmt.util as cmu
import gitutil
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
    cmr.create_report(reports)


def process_config_queue(
    cfg_dir: str,
    github_cfg: str, # e.g. github_wdf_sap_corp
    cfg_repo_path: str, # e.g. kubernetes/cc-config
    type_name: str=None,
    name: str=None,
    tgt_ref: str='refs/heads/master',
):
    '''
    Iterates to be deleted cfg_queue_entries with optional cfg_target filter.
    Processes very first supported entry and terminates.
    '''

    # ensure cfg_target filter is correct (None or name and type_name)
    if not type_name and not name:
        cfg_target = None
    elif not type_name or not name:
        logger.error('when specifying cfg_target filter, both name and type_name must be given')
        return

    cfg_metadata = cfg_mgmt.model.cfg_metadata_from_cfg_dir(cfg_dir=cfg_dir)
    cfg_factory = model.ConfigFactory.from_cfg_dir(
        cfg_dir=cfg_dir,
        disable_cfg_element_lookup=True,
    )
    github_cfg = cfg_factory.github(github_cfg)
    git_helper = gitutil.GitHelper(
        repo=cfg_dir,
        github_cfg=github_cfg,
        github_repo_path=cfg_repo_path,
    )

    cfg_target = cfg_mgmt.model.CfgTarget(
        type=type_name,
        name=name,
    )

    for cfg_queue_entry in cmu.iter_cfg_queue_entries_to_be_deleted(
        cfg_metadata=cfg_metadata,
        cfg_target=cfg_target,
    ):
        if not cfg_mgmt.rotate.delete_cfg_element(
            cfg_dir=cfg_dir,
            cfg_queue_entry=cfg_queue_entry,
            cfg_fac=cfg_factory,
            cfg_metadata=cfg_metadata,
            git_helper=git_helper,
            target_ref=tgt_ref,
        ):
            continue

        # stop after first successful rotation (avoid causing too much trouble at one time
        return
    logger.info('no to be deleted config queue entry found')


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
        cfg_dir=cfg_dir,
        cfg_element=cfg_element,
        target_ref=tgt_ref,
        github_cfg=github_cfg,
        cfg_metadata=cfg_metadata,
        github_repo_path=cfg_repo_path,
    )
