import logging
import typing

import checkmarx.model as cmx_model
import checkmarx.util
import gci
import github.compliance.model as gcm
import github.compliance.issue as gciss

logger: logging.Logger = logging.getLogger(__name__)


def scan_result_group_collection(
    results: tuple[cmx_model.ScanResult],
    severity_threshold: str,
):
    def classification_callback(result: cmx_model.ScanResult) -> gcm.Severity:
        max_sev = checkmarx.util.greatest_severity(result)
        return checkmarx.util.checkmarx_severity_to_github_severity(max_sev)

    def findings_callback(result: cmx_model.ScanResult) -> bool:
        max_sev = checkmarx.util.greatest_severity(result)
        return max_sev and max_sev >= threshold

    threshold = cmx_model.Severity.from_str(severity_threshold)

    return gcm.ScanResultGroupCollection(
        results=tuple(results),
        issue_type=gciss._label_checkmarx,
        classification_callback=classification_callback,
        findings_callback=findings_callback,
    )


def scan_sources(
    checkmarx_cfg_name: str,
    component_descriptor: gci.componentmodel.ComponentDescriptor,
    team_id: str = None,
    threshold: str = 'medium',
    exclude_paths: typing.Sequence[str] = (),
    include_paths: typing.Sequence[str] = (),
    force: bool = False,
) -> cmx_model.FinishedScans:
    checkmarx_cfg = checkmarx.util.get_checkmarx_cfg(checkmarx_cfg_name)
    if not team_id:
        team_id = checkmarx_cfg.team_id()

    logger.info(f'using checkmarx team: {team_id}')

    checkmarx_client = checkmarx.util.create_checkmarx_client(checkmarx_cfg)

    scans = checkmarx.util.scan_sources(
        component_descriptor=component_descriptor,
        cx_client=checkmarx_client,
        team_id=team_id,
        exclude_paths=exclude_paths,
        include_paths=include_paths,
        force=force,
    )

    checkmarx.util.print_scans(
        scans=scans,
        threshold=cmx_model.Severity.from_str(threshold),
        routes=checkmarx_client.routes,
    )

    return scans
