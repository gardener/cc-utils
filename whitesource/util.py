import asyncio
import concurrent.futures
import functools
import logging
import os.path
import tempfile
import typing

import tabulate
import requests

import gci.componentmodel as cm

import ccc.github
import ccc.oci
import ci.util
import dso.model
import mailutil
import oci
import product.util
import reutil
import tarutil
import whitesource.client
import whitesource.component
import whitesource.model


logger = logging.getLogger(__name__)


def _mk_ctx():
    scanned = 1

    def increment():
        nonlocal scanned
        scanned += 1

    def get():
        return scanned

    return get, increment


def _mk_exitcodes():
    exit_codes = []

    def add(exit_code: int):
        nonlocal exit_codes
        exit_codes.append(exit_code)

    def get():
        return exit_codes

    return get, add


def generate_reporting_tables(
    below: typing.List[whitesource.model.WhiteSrcProject],
    above: typing.List[whitesource.model.WhiteSrcProject],
    tablefmt,
):
    # monkeypatch: disable html escaping
    tabulate.htmlescape = lambda x: x

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
    data: typing.List[whitesource.model.ProjectSummary],
    table_header,
    tablefmt,
):
    sorted_data = sorted(
        data,
        key=lambda p: p.greatestCvssv3,
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
    data: typing.List[whitesource.model.ProjectSummary],
):
    table_data = (
        (
            project.name,
            project.greatestCve,
            project.greatestCvssv3,
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


def scan_artifact_with_white_src(
    extra_whitesource_config: typing.Union[None, dict],
    scan_artifact: dso.model.ScanArtifact,
    whitesource_client: whitesource.client.WhitesourceClient,
) -> int:

    logger.debug('init scan')
    with tempfile.NamedTemporaryFile() as tmp_file:
        if scan_artifact.access.type is cm.AccessType.GITHUB:
            logger.debug('pulling from github')
            github_api = ccc.github.github_api_from_gh_access(access=scan_artifact.access)
            github_repo = github_api.repository(
                owner=scan_artifact.access.org_name(),
                repository=scan_artifact.access.repository_name(),
            )
            # guess git-ref for the given version
            commit_hash = product.util.guess_commit_from_source(
                artifact_name=scan_artifact.name,
                commit_hash=scan_artifact.access.commit,
                ref=scan_artifact.access.ref,
                github_repo=github_repo,
            )
            exclude_regexes = ()
            include_regexes = ()

            if scan_artifact.label is not None:
                if scan_artifact.label.path_config is not None:
                    exclude_regexes = scan_artifact.label.path_config.exclude_paths
                    include_regexes = scan_artifact.label.path_config.include_paths

            path_filter_func = reutil.re_filter(
                exclude_regexes=exclude_regexes,
                include_regexes=include_regexes
            )
            whitesource.component.download_component(
                logger=logger,
                github_repo=github_repo,
                path_filter_func=path_filter_func,
                ref=commit_hash,
                target=tmp_file,
            )
        elif scan_artifact.access.type is cm.AccessType.OCI_REGISTRY:
            logger.debug('pulling from oci registry')
            oci_client = ccc.oci.oci_client()
            tar_gen = oci.image_layers_as_tarfile_generator(
                image_reference=scan_artifact.access.imageReference,
                oci_client=oci_client,
                include_config_blob=False,
            )

            fake_gen = tarutil._FilelikeProxy(generator=tar_gen)
            while chunk := fake_gen.read():
                tmp_file.write(chunk)
        else:
            raise NotImplementedError

        tmp_file.seek(0)

        logger.info(f'sending {scan_artifact.name} to whitesource-api-extension')

        exit_code, res = asyncio.run(
            whitesource_client.upload_to_project(
                extra_whitesource_config=extra_whitesource_config,
                file=tmp_file,
                project_name=scan_artifact.name,
                length=os.path.getsize(tmp_file.name),
            )
        )
        logger.info(res['message'])
        logger.info('scan complete')
        # TODO save scanned commit hash or tag in project tag show scanned version
        # version for agent will create a new project
        # https://whitesource.atlassian.net/wiki/spaces/WD/pages/34046170/HTTP+API+v1.1#HTTPAPIv1.1-ProjectTags
        return int(exit_code)


def _scan_artifact(
    artifact: dso.model.ScanArtifact,
    extra_whitesource_config: typing.Union[None, dict],
    whitesource_client,
    len_artifacts: int,
    get_scanned_count: typing.Callable,
    increment_scanned_count: typing.Callable[[], int],
    add_exit_code: typing.Callable[[], int],
):
    logger.info(f'scanning {get_scanned_count()}/{len_artifacts} - {artifact.name}')
    increment_scanned_count()
    exit_code = scan_artifact_with_white_src(
        extra_whitesource_config=extra_whitesource_config,
        scan_artifact=artifact,
        whitesource_client=whitesource_client,
    )
    add_exit_code(exit_code)


def scan_artifacts(
    whitesource_client: whitesource.client.WhitesourceClient,
    extra_whitesource_config: typing.Union[None, dict],
    max_workers: int,
    artifacts: typing.List[dso.model.ScanArtifact],
) -> typing.List[int]:

    get_scanned_count, increment_scanned_count = _mk_ctx()
    get_exit_codes, add_exit_code = _mk_exitcodes()
    logger.info(f'{len(artifacts)} artifacts to scan')

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        executor.map(functools.partial(
            _scan_artifact,
            extra_whitesource_config=extra_whitesource_config,
            whitesource_client=whitesource_client,
            len_artifacts=len(artifacts),
            get_scanned_count=get_scanned_count,
            increment_scanned_count=increment_scanned_count,
            add_exit_code=add_exit_code,
        ), artifacts)
    return get_exit_codes()


def send_vulnerability_report(
    notification_recipients: typing.Union[None, typing.List[str]],
    cve_threshold: float,
    product_name: str,
    below: typing.List[whitesource.model.WhiteSrcProject],
    above: typing.List[whitesource.model.WhiteSrcProject],
):
    # generate html reporting table for email notifications
    tables = generate_reporting_tables(
        below=below,
        above=above,
        tablefmt='html',
    )

    body = assemble_mail_body(
        tables=tables,
        threshold=cve_threshold,
    )

    # get standard cfg set for email cfg
    default_cfg_set_name = ci.util.current_config_set_name()
    cfg_factory = ci.util.ctx().cfg_factory()
    cfg_set = cfg_factory.cfg_set(default_cfg_set_name)

    mailutil._send_mail(
        email_cfg=cfg_set.email(),
        recipients=notification_recipients,
        mail_template=body,
        subject=f'[Action Required] ({product_name}) WhiteSource Vulnerability Report',
        mimetype='html',
    )


def parse_filters(
    filters: typing.Optional[typing.List[dict]],
) -> typing.List[whitesource.model.WhiteSourceFilterCfg]:
    l: typing.List[whitesource.model.WhiteSourceFilterCfg] = []
    if not filters:
        return []

    logger.info(f'found filter! {filters=}')
    for f in filters:
        l.append(whitesource.model.WhiteSourceFilterCfg(
            type=whitesource.model.FilterType(f.get('type')),
            action=whitesource.model.ActionType(f.get('action')),
            match=f.get('match'),
        ))

    # sorting filters, component filters have to be first elements
    # if component is excluded, its artifacts are excluded as well
    return sorted(l, key=lambda k: str(k.type))


def delete_all_projects_from_product(
    product_token: str,
    user_token: str,
    api_endpoint: str,
):

    def request(
        method: str,
        *args, **kwargs
    ):
        res = requests.request(
            method=method,
            *args, **kwargs,
        )
        if not res.ok:
            print(f'{method} request to url {res.url} failed with {res.status_code=} {res.reason=}')
        return res

    def get_all_projects(
        product_token: str,
        user_token: str,
    ):
        body = {
            'requestType': 'getAllProjects',
            'userKey': user_token,
            'productToken': product_token,
        }

        res = request(
            method='POST',
            url=api_endpoint,
            headers={'content-type': 'application/json'},
            json=body,
        )

        return res.json()['projects']

    def delete_project(
        project_token: str,
        user_token: str,
        product_token: str,
    ):
        logger.info(f'deleting {project_token=}')
        body = {
            'requestType': 'deleteProject',
            'userKey': user_token,
            'productToken': product_token,
            'projectToken': project_token,
        }

        request(
            method='POST',
            url=api_endpoint,
            headers={'content-type': 'application/json'},
            json=body,
        )

    for p in get_all_projects(
        product_token=product_token,
        user_token=user_token,
    ):
        delete_project(
            product_token=product_token,
            project_token=p.get('projectToken'),
            user_token=user_token,
        )
    logger.info('done')


def split_projects_summaries_on_threshold(
    projects_summaries: typing.List[whitesource.model.ProjectSummary],
    threshold: float,
) -> typing.Sequence[whitesource.model.ProjectSummary]:
    projects_summaries_below_threshold = []
    projects_summaries_above_threshold = []

    for p in projects_summaries:
        if p.greatestCvssv3 > threshold:
            projects_summaries_above_threshold.append(p)
        else:
            projects_summaries_below_threshold.append(p)

    return projects_summaries_below_threshold, projects_summaries_above_threshold


def check_exitcodes(
    product_name: str,
    notification_recipients: list,
    exitcodes: list,
):
    """
    If any exit code != 0, a notification is send to all recipients.
    """
    if any((lambda: e != 0)() for e in exitcodes):
        logger.warning('some scans failed')
        logger.info(f'notifying {notification_recipients} about failed scan')
    else:
        logger.info('all scans reported a successful execution')
