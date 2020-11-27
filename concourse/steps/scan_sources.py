from datetime import datetime
import tempfile
import typing

import ccc.github
import ci.util
import checkmarx.util
import mail.model
import product.model
import product.util
import reutil
import sdo.model
import sdo.util
import whitesource.client
import whitesource.component
import whitesource.util
import product.v2


import gci.componentmodel as cm


def scan_sources_and_notify(
    checkmarx_cfg_name: str,
    component_descriptor_path: str,
    email_recipients,
    team_id: str,
    threshold: int = 40,
    exclude_paths: typing.Sequence[str] = (),
    include_paths: typing.Sequence[str] = (),
):
    checkmarx_client = checkmarx.util.create_checkmarx_client(checkmarx_cfg_name)

    scans = checkmarx.util.scan_sources(
        component_descriptor_path=component_descriptor_path,
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
    #TODO codeowner recipient:


def scan_component_with_whitesource(
    whitesource_cfg_name: str,
    product_token: str,
    component_descriptor_path: str,
    extra_whitesource_config: dict,
    requester_mail: str,
    cve_threshold: float,
    notification_recipients: list,
):

    # create whitesource client
    ci.util.info('creating whitesource client')
    ws_client = whitesource.util.create_whitesource_client(
        whitesource_cfg_name=whitesource_cfg_name,
    )

    # parse component_descriptor
    ci.util.info('parsing component descriptor')
    component_descriptor = cm.ComponentDescriptor.from_dict(
        ci.util.parse_yaml_file(component_descriptor_path)
    )

    product_name = component_descriptor.component.name

    components = product.v2.components(component_descriptor_v2=component_descriptor)

    # get scan artifacts with configured label
    scan_artifacts_gen = whitesource.component._get_scan_artifacts_from_components(components)

    scan_artifacts = tuple(scan_artifacts_gen)
    ci.util.info(f'will scan {len(scan_artifacts)} artifact')

    for scan_artifact in scan_artifacts:
        scan_artifact_with_ws(
            extra_whitesource_config=extra_whitesource_config,
            product_token=product_token,
            requester_mail=requester_mail,
            scan_artifact=scan_artifact,
            ws_client=ws_client,
        )

    notify_users(
        notification_recipients=notification_recipients,
        cve_threshold=cve_threshold,
        ws_client=ws_client,
        product_name=product_name,
        product_token=product_token,
    )


def scan_artifact_with_ws(
    extra_whitesource_config: typing.Dict,
    product_token: str,
    requester_mail: str,
    scan_artifact: sdo.model.ScanArtifact,
    ws_client: whitesource.client.WhitesourceClient,
):
    clogger = sdo.util.component_logger(scan_artifact.name)

    clogger.info('init scan')
    github_api = ccc.github.github_api_from_gh_access(access=scan_artifact.access)
    github_repo = github_api.repository(
        owner=scan_artifact.access.org_name(),
        repository=scan_artifact.access.repository_name(),
    )

    clogger.info('guessing commit hash')
    # guess git-ref for the given component's version
    commit_hash = product.util.guess_commit_from_source(
        artifact_name=scan_artifact.name,
        commit_hash=scan_artifact.access.commit,
        ref=scan_artifact.access.ref,
        github_repo=github_repo,
    )

    path_filter_func = reutil.re_filter(
        exclude_regexes=scan_artifact.label.path_config.exclude_paths,
        include_regexes=scan_artifact.label.path_config.include_paths,
    )

    # store in tmp file
    with tempfile.TemporaryFile() as tmp_file:
        # download whitesource component
        clogger.info('downloading component for scan')
        component_filename = whitesource.component.download_component(
            clogger=clogger,
            github_repo=github_repo,
            path_filter_func=path_filter_func,
            ref=commit_hash,
            target=tmp_file,
        )
        # needed
        tmp_file.seek(0)

        # POST component>
        clogger.info('POST project')
        ws_client.post_project(
            extra_whitesource_config=extra_whitesource_config,
            file=tmp_file,
            filename=component_filename,
            product_token=product_token,
            project_name=scan_artifact.name,
            requester_email=requester_mail,
        )
        # TODO save scanned commit hash or tag in project tag show scanned version
        # version for agent will create a new project
        # https://whitesource.atlassian.net/wiki/spaces/WD/pages/34046170/HTTP+API+v1.1#HTTPAPIv1.1-ProjectTags


def notify_users(
    ws_client: whitesource.client.WhitesourceClient,
    product_token: str,
    cve_threshold: float,
    notification_recipients: typing.List[str],
    product_name: str
):
    # get all projects
    ci.util.info('retrieving all projects')
    projects = ws_client.get_all_projects_of_product(product_token=product_token)

    # generate reporting table for console
    tables = whitesource.util.generate_reporting_tables(
        projects=projects,
        threshold=cve_threshold,
        tablefmt='simple',
    )

    whitesource.util.print_cve_tables(tables=tables)

    if len(notification_recipients) > 0:
        # generate reporting table for notification
        tables = whitesource.util.generate_reporting_tables(
            projects=projects,
            threshold=cve_threshold,
            tablefmt='html',
        )

        # get product risk report
        ci.util.info('retrieving product risk report')
        prr = ws_client.get_product_risk_report(product_token=product_token)

        # assemble html body
        body = whitesource.util.assemble_mail_body(
            tables=tables,
            threshold=cve_threshold,
        )

        # send mail
        ci.util.info('sending notification')
        now = datetime.now()
        fname = f'{product_name}-{now.year}.{now.month}-{now.day}-product-risk-report.pdf'

        attachment = mail.model.Attachment(
            mimetype_main='application',
            mimetype_sub='pdf',
            bytes=prr.content,
            filename=fname,
        )

        whitesource.util.send_mail(
            body=body,
            recipients=notification_recipients,
            product_name=product_name,
            attachments=[attachment],
        )
