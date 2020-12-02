import datetime
import functools
import tempfile
import typing

import tabulate

import ccc.github
import ci.util
import mailutil
import mail.model
import product.util
import reutil
import sdo.util
import sdo.model
import whitesource.client
import whitesource.component
import whitesource.model


@functools.lru_cache()
def create_whitesource_client(
    whitesource_cfg_name: str,
) -> whitesource.client.WhitesourceClient:
    cfg_fac = ci.util.ctx().cfg_factory()
    ws_config = cfg_fac.whitesource(whitesource_cfg_name)

    return whitesource.client.WhitesourceClient(
        api_key=ws_config.api_key(),
        extension_endpoint=ws_config.extension_endpoint(),
        wss_api_endpoint=ws_config.wss_api_endpoint(),
        wss_endpoint=ws_config.wss_endpoint(),
        ws_creds=ws_config.credentials()
    )


def generate_reporting_tables(
    projects: typing.List[whitesource.model.WhitesourceProject],
    threshold: float,
    tablefmt,
):
    # monkeypatch: disable html escaping
    tabulate.htmlescape = lambda x: x

    # split respecting CVSS-V3 threshold
    above: typing.List[whitesource.model.WhitesourceProject] = []
    below: typing.List[whitesource.model.WhitesourceProject] = []

    for project in projects:
        if float(project.max_cve()[1]) > threshold:
            above.append(project)
        else:
            below.append(project)

    def _sort_projects_by_cve(
        projects: typing.List[whitesource.model.WhitesourceProject],
        descending=True,
    ):
        return sorted(
            projects,
            key=lambda p: p.max_cve()[1],
            reverse=descending,
        )

    # sort tables descending by CVSS-V3
    below = _sort_projects_by_cve(projects=below)
    above = _sort_projects_by_cve(projects=above)

    ttable_header = (
        'Component',
        'Greatest CVSS-V3',
        'Corresponding CVE',
    )
    ttables = []

    for source in above, below:
        if len(source) == 0:
            ttables.append('')
            continue
        ttable_data = (
            (
                project.name,
                project.max_cve()[0],
                project.max_cve()[1],
            ) for project in projects
        )

        ttable = tabulate.tabulate(
            headers=ttable_header,
            tabular_data=ttable_data,
            tablefmt=tablefmt,
            colalign=('left', 'center', 'center'),
        )

        ttables.append(ttable)

    return ttables


def assemble_mail_body(
    tables: typing.List,
    threshold: float,
):
    return f'''
        <div>
            <p>
                Note: you receive this E-Mail, because you were configured as a mail recipient
                (see .ci/pipeline_definitions)
                To remove yourself, search for your e-mail address in said file and remove it.
            </p>
            <br></br>
            <p>
                The following component(s) have a CVSS-V3 greater than the configured threshold of
                {threshold}. It is configured at the
                <a href="https://github.wdf.sap.corp/kubernetes/cc-config">
                    pipeline definition
                </a>.
            </p>
            {tables[0]}
            <br></br>
            <br></br>
            <p>
                These are the remaining component(s) with a CVSS-V3 lower than {threshold}
            </p>
            {tables[1]}
            <br></br>
            <br></br>
            <p>
                WhiteSource triage has to be done on the
                <a href="https://saas.whitesourcesoftware.com/Wss/WSS.html#!alertsReport">
                    WhiteSource Alert Reporting
                </a>
                page. Appropriate filters have to be applied manually,
                "Gardener" is a matching keyword.
            </p>
        </div>
    '''


def send_mail(
    body,
    recipients: list,
    product_name: str,
    attachments: typing.Sequence[mail.model.Attachment],
):

    # get standard cfg set for email cfg
    default_cfg_set_name = ci.util.current_config_set_name()
    cfg_factory = ci.util.ctx().cfg_factory()
    cfg_set = cfg_factory.cfg_set(default_cfg_set_name)

    mailutil._send_mail(
        email_cfg=cfg_set.email(),
        recipients=recipients,
        mail_template=body,
        subject=f'[Action Required] ({product_name}) WhiteSource Vulnerability Report',
        mimetype='html',
        attachments=attachments,
    )


def print_cve_tables(tables):
    print()
    print('\n\n'.join(tables))
    print()


def notify_users(
    ws_client: whitesource.client.WhitesourceClient,
    product_token: str,
    cve_threshold: float,
    notification_recipients: typing.List[str],
    product_name: str
):
    ci.util.info('retrieving all projects')
    projects = ws_client.get_all_projects_of_product(product_token=product_token)

    # generate simple reporting table for console output
    tables = generate_reporting_tables(
        projects=projects,
        threshold=cve_threshold,
        tablefmt='simple',
    )

    print_cve_tables(tables=tables)

    if len(notification_recipients) > 0:
        # generate html reporting table for email notifications
        tables = generate_reporting_tables(
            projects=projects,
            threshold=cve_threshold,
            tablefmt='html',
        )
        ci.util.info('retrieving product risk report')
        prr = ws_client.get_product_risk_report(product_token=product_token)

        body = assemble_mail_body(
            tables=tables,
            threshold=cve_threshold,
        )

        ci.util.info('sending notification')
        attachment_file_name = datetime.datetime.now().strftime('%Y-%m-%d-product-risk-report.pdf')
        attachment = mail.model.Attachment(
            mimetype_main='application',
            mimetype_sub='pdf',
            bytes=prr.content,
            filename=attachment_file_name,
        )

        send_mail(
            body=body,
            recipients=notification_recipients,
            product_name=product_name,
            attachments=[attachment],
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
    # guess git-ref for the given version
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

    with tempfile.TemporaryFile() as tmp_file:
        clogger.info('downloading component for scan')
        component_filename = whitesource.component.download_component(
            clogger=clogger,
            github_repo=github_repo,
            path_filter_func=path_filter_func,
            ref=commit_hash,
            target=tmp_file,
        )
        # don't change the following line, lest things no longer work
        # sets the file position at the offset 0 == start of the file
        tmp_file.seek(0)

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
