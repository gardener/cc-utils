import functools
import json

import tabulate

import ci.util
import whitesource.client
import mailutil


@functools.lru_cache()
def create_whitesource_client(whitesource_cfg_name: str):
    cfg_fac = ci.util.ctx().cfg_factory()
    return whitesource.client.WhitesourceClient(cfg_fac.whitesource(whitesource_cfg_name))


def generate_reporting_tables(report,
                              threshold: int,
                              tablefmt):

    # monkeypatch: disable html escaping
    tabulate.htmlescape = lambda x: x

    # split respecting CVSS-V3 threshold
    above = {}
    below = {}

    for lib, dic in report.items():
        if float(dic['CVSS-V3']) > threshold:
            above[lib] = dic
        else:
            below[lib] = dic

    def _sort_struct(struct):
        return sorted(struct.items(),
                      key=lambda k_v: k_v[1]['CVSS-V3'],
                      reverse=True)

    # sort tables descending by CVSS-V3
    below = _sort_struct(below)
    above = _sort_struct(above)

    ttable_header = ('Component', 'Greatest CVSS-V3', 'Corresponding CVE')
    ttables = []

    for source in above, below:
        if len(source) == 0:
            ttables.append("")
            continue
        ttable_data = (
            (
                component,
                dic["CVSS-V3"],
                dic["CVE"]
            ) for component, dic, in source
        )

        ttable = tabulate.tabulate(
            headers=ttable_header,
            tabular_data=ttable_data,
            tablefmt=tablefmt,
            colalign=('left', 'center', 'center')
        )

        ttables.append(ttable)

    return ttables


def assemble_mail_body(tables: list,
                       threshold: float):
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
                page. Appropriate filters have to applied manually, "Gardener" is a matching keyword.
            </p>
        </div>
    '''


def find_greatest_cve(projects,
                      client):
    report = {}

    # get all projects for product
    for project in projects["projects"]:
        pname = project["projectName"]
        ptoken = project["projectToken"]

        # get vulnerability report per project
        ci.util.info(f'retrieving project vulnerability report for {pname}')
        pvr = client.get_project_vulnerability_report(project_token=ptoken)

        # find greatest cve per project
        for vul in json.loads(pvr.content)["vulnerabilities"]:
            try:
                if report.get(pname) is None or vul["cvss3_score"] > report[pname]["CVSS-V3"]:
                    report[pname] = {
                        "CVSS-V3": vul["cvss3_score"],
                        "CVE": vul["name"],
                    }
            except KeyError:
                # WS are ignored
                pass


def send_mail(body,
              recipients: list,
              landscape: str,
              pdfs=[]):

    # get standard cfg set for email cfg
    default_cfg_set_name = ci.util.current_config_set_name()
    cfg_factory = ci.util.ctx().cfg_factory()
    cfg_set = cfg_factory.cfg_set(default_cfg_set_name)

    mailutil._send_mail(
        email_cfg=cfg_set.email(),
        recipients=recipients,
        mail_template=body,
        subject=f'[Action Required] ({landscape}) WhiteSource Vulnerability Report',
        mimetype='html',
        pdfs=pdfs
    )
