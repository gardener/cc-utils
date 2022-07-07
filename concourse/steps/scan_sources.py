import logging
import typing

import gci.componentmodel as cm

import ccc.whitesource
import checkmarx.model as cmx_model
import checkmarx.util
import cnudie.retrieve
import gci
import github.compliance.model as gcm
import github.compliance.issue as gciss
import whitesource.component
import whitesource.model
import whitesource.util


logger: logging.Logger = logging.getLogger(__name__)


def scan_result_group_collection(
    results: tuple[cmx_model.ScanResult],
    severity_threshold: str,
):
    def classification_callback(result: cmx_model.ScanResult) -> gcm.Severity:
        max_sev = checkmarx.util.greatest_severity(result)
        return checkmarx.util.checkmarx_severity_to_github_severity(max_sev)

    def findings_callback(result: cmx_model.ScanResult):
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


def scan_component_with_whitesource(
    whitesource_cfg_name: str,
    component_descriptor: cm.ComponentDescriptor,
    cve_threshold: float,
    extra_whitesource_config: dict = None,
    notification_recipients: list = [],
    filters: list = None,
    max_workers=4,
):
    filters = whitesource.util.parse_filters(filters=filters)

    whitesource_client = ccc.whitesource.make_client(
        whitesource_cfg_name=whitesource_cfg_name,
    )

    components = cnudie.retrieve.components(component=component_descriptor)

    # create scan_artifact generator with filters in consideration
    scan_artifacts_gen = whitesource.component.scan_artifacts_gen(
        components,
        filters=filters,
    )
    scan_artifacts = tuple(scan_artifacts_gen)

    # perform whitesource scans and gather exitcodes
    exit_codes = whitesource.util.scan_artifacts(
        whitesource_client=whitesource_client,
        extra_whitesource_config=extra_whitesource_config,
        max_workers=max_workers,
        artifacts=scan_artifacts,
    )

    product_name = component_descriptor.component.name

    # notify recipients if at least one scan returned an exitcode != 0
    whitesource.util.check_exitcodes(
        product_name=product_name,
        notification_recipients=notification_recipients,
        exitcodes=exit_codes,
    )

    # get all projects of the product to generate results
    projects = whitesource_client.projects_of_product()

    # summarise projects (name, greatest CVE + score)
    projects_summaries = [
        whitesource.model.ProjectSummary(
            name=p.name,
            greatestCve=p.max_cve()[0],
            greatestCvssv3=float(p.max_cve()[1]),
        )
        for p in projects
    ]

    # create two lists, containing projects below and above given cvssv3 threshold
    below, above = whitesource.util.split_projects_summaries_on_threshold(
        projects_summaries=projects_summaries,
        threshold=cve_threshold,
    )

    # print results on console
    tables = whitesource.util.generate_reporting_tables(
        below=below,
        above=above,
        tablefmt='simple',
    )
    print('\n' + '\n\n'.join(tables) + '\n')

    # send result tables via email
    if not len(notification_recipients) > 0:
        logger.warning('No recipients defined. No emails will be sent...')
        return

    logger.info(f'sending vulnerability report to {notification_recipients}')
    whitesource.util.send_vulnerability_report(
        notification_recipients=notification_recipients,
        cve_threshold=cve_threshold,
        product_name=product_name,
        below=below,
        above=above,
    )
