import functools

import tabulate
import typing

import ci.util
import mailutil
import mail.model
import whitesource.client
import whitesource.model


@functools.lru_cache()
def create_whitesource_client(
    whitesource_cfg_name: str,
):
    cfg_fac = ci.util.ctx().cfg_factory()
    return whitesource.client.WhitesourceClient(cfg_fac.whitesource(whitesource_cfg_name))


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
    component_name: str,
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
        subject=f'[Action Required] ({component_name}) WhiteSource Vulnerability Report',
        mimetype='html',
        attachments=attachments,
    )


def print_cve_tables(tables):
    print()
    print('\n\n'.join(tables))
    print()
