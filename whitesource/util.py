import asyncio
import functools
import tempfile
import typing

import tabulate

import ccc.github
import ci.util
import mailutil
import product.util
import reutil
import dso.util
import dso.model
import whitesource.client
import whitesource.component
import whitesource.model


clogger = dso.util.component_logger(__name__)


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
        ws_creds=ws_config.credentials(),
        product_token=ws_config.product_token(),
    )


def generate_reporting_tables(
    projects: typing.List[whitesource.model.WhiteSrcProject],
    threshold: float,
    tablefmt,
):
    # monkeypatch: disable html escaping
    tabulate.htmlescape = lambda x: x

    # split respecting CVSS-V3 threshold
    above: typing.List[whitesource.model.WhiteSrcDisplayProject] = []
    below: typing.List[whitesource.model.WhiteSrcDisplayProject] = []

    for project in projects:
        max_cve = project.max_cve()
        display_project = whitesource.model.WhiteSrcDisplayProject(
            name=project.name,
            highest_cve_name=max_cve[0],
            highest_cve_score=float(max_cve[1]),
        )
        if display_project.highest_cve_score > threshold:
            above.append(display_project)
        else:
            below.append(display_project)

    ttable_header = (
        'Component',
        'Greatest CVSS-V3',
        'Corresponding CVE',
    )
    if above:
        above_table = _create_table(
            data=above,
            table_header=ttable_header,
            tablefmt=tablefmt,
        )
    else:
        above_table = ''

    if below:
        below_table = _create_table(
            data=below,
            table_header=ttable_header,
            tablefmt=tablefmt,
        )
    else:
        below_table = ''

    return [above_table, below_table]


def _create_table(
    data: typing.List[whitesource.model.WhiteSrcDisplayProject],
    table_header,
    tablefmt,
):
    sorted_data = sorted(
        data,
        key=lambda p: p.highest_cve_score,
        reverse=True,
    )
    table = _gen_table_from_data(
        table_headers=table_header,
        tablefmt=tablefmt,
        data=sorted_data,
    )
    return table


def _gen_table_from_data(
    table_headers,
    tablefmt,
    data: typing.List[whitesource.model.WhiteSrcDisplayProject],
):
    table_data = (
        (
            project.name,
            project.highest_cve_name,
            project.highest_cve_score,
        ) for project in data
    )
    table = tabulate.tabulate(
        headers=table_headers,
        tabular_data=table_data,
        tablefmt=tablefmt,
        colalign=('left', 'center', 'center'),
    )
    return table


def assemble_mail_body(
    tables: typing.List,
    threshold: float,
):
    if tables[0] == '':
        above_table_part = ''
    else:
        above_table_part = f'''
        <p>
            The following component(s) have a CVSS-V3 greater than the configured threshold of
            {threshold}.
        </p>
        {tables[0]}
        <br></br>
        '''
    if tables[1] == '':
        below_table_part = ''
    else:
        below_table_part = f'''
        <p>
            These component(s) have a CVSS-V3 score lower than {threshold}
        </p>
        {tables[1]}
        <br></br>
        '''
    return f'''
        <div>
            <p>
                Note: you receive this E-Mail, because you were configured as a mail recipient
                (see .ci/pipeline_definitions)
                To remove yourself, search for your e-mail address in said file and remove it.
            </p>
            <br></br>
            {above_table_part}
            {below_table_part}
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
    )


def print_cve_tables(tables):
    print('\n' + '\n\n'.join(tables) + '\n')


def notify_users(
    ws_client: whitesource.client.WhitesourceClient,
    cve_threshold: float,
    notification_recipients: typing.List[str],
    product_name: str
):
    clogger.info('retrieving all projects')
    projects = ws_client.get_all_projects_of_product()

    if len(projects) == 0:
        ci.util.warning(
            f'No projects found in product {product_name}. No data to report. Exiting...',
        )
        return

    clogger.info('generate simple reporting table for console output')
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

        body = assemble_mail_body(
            tables=tables,
            threshold=cve_threshold,
        )

        # TODO fix mail pdf attachment
        # add line break after 72 lines to avoid line too long error
        clogger.info('sending notification')
        send_mail(
            body=body,
            recipients=notification_recipients,
            product_name=product_name,
        )
    else:
        clogger.warning('No recipients defined. No emails will be sent...')


def scan_artifact_with_white_src(
    extra_whitesource_config: typing.Dict,
    requester_mail: str,
    scan_artifact: dso.model.ScanArtifact,
    ws_client: whitesource.client.WhitesourceClient,
):
    clogger = dso.util.component_logger(scan_artifact.name)

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
    exclude_regexes = ''
    include_regexes = ''

    if scan_artifact.label is not None:
        if scan_artifact.label.path_config is not None:
            exclude_regexes = scan_artifact.label.path_config.exclude_paths
            include_regexes = scan_artifact.label.path_config.include_paths

    path_filter_func = reutil.re_filter(
        exclude_regexes=exclude_regexes,
        include_regexes=include_regexes
    )

    with tempfile.TemporaryFile() as tmp_file:
        clogger.info('downloading component for scan')
        file_size = whitesource.component.download_component(
            clogger=clogger,
            github_repo=github_repo,
            path_filter_func=path_filter_func,
            ref=commit_hash,
            target=tmp_file,
        )

        # don't change the following line, lest things no longer work
        # sets the file position at the offset 0 == start of the file
        tmp_file.seek(0)

        clogger.info('sending component to scan backend...')

        res = asyncio.run(
            ws_client.upload_to_project(
                extra_whitesource_config=extra_whitesource_config,
                file=tmp_file,
                project_name=scan_artifact.name,
                requester_email=requester_mail,
                length=file_size,
            )
        )
        clogger.info(res['message'])
        clogger.info('scan complete')
        # TODO save scanned commit hash or tag in project tag show scanned version
        # version for agent will create a new project
        # https://whitesource.atlassian.net/wiki/spaces/WD/pages/34046170/HTTP+API+v1.1#HTTPAPIv1.1-ProjectTags
