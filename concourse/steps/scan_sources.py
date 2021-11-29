import logging
import typing

import gci.componentmodel as cm

import ccc.whitesource
import checkmarx.util
import cnudie.retrieve
import whitesource.component
import whitesource.model
import whitesource.util


logger: logging.Logger = logging.getLogger(__name__)


def scan_sources_and_notify(
    checkmarx_cfg_name: str,
    component_descriptor: cm.ComponentDescriptor,
    email_recipients,
    team_id: str,
    threshold: int = 40,
    exclude_paths: typing.Sequence[str] = (),
    include_paths: typing.Sequence[str] = (),
):
    checkmarx_client = checkmarx.util.create_checkmarx_client(checkmarx_cfg_name)

    scans = checkmarx.util.scan_sources(
        component_descriptor=component_descriptor,
        cx_client=checkmarx_client,
        team_id=team_id,
        threshold=threshold,
        exclude_paths=exclude_paths,
        include_paths=include_paths,
    )

    checkmarx.util.print_scans(
        scans=scans,
        routes=checkmarx_client.routes,
    )

    checkmarx.util.send_mail(
        scans=scans,
        threshold=threshold,
        email_recipients=email_recipients,
        routes=checkmarx_client.routes,
    )
    #TODO codeowner recipient


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
