import datetime
import pathlib
import tabulate
import tempfile
import typing

import ccc.github
import ci.util
import dso.model
import dso.util
import gitutil


def _dependabot_yaml_in_known_places(repo_dir: str) -> bool:
    clogger = dso.util.component_logger(__name__)
    DEPENDABOT_YAML_PATHS = (
        '.dependabot.yaml',
        '.dependabot.yml',
        '.github/.dependabot.yaml',
        '.github/.dependabot.yml',
        'dependabot.yaml',
        'dependabot.yml',
        '.github/dependabot.yaml',
        '.github/dependabot.yml'
    )

    repo_dir = ci.util.existing_dir(pathlib.Path(repo_dir))
    if not repo_dir.joinpath('.git').is_dir():
        raise ValueError(f'not a git root directory: {repo_dir}')

    for path in DEPENDABOT_YAML_PATHS:
        dependabot_yaml = repo_dir.joinpath(path)
        if dependabot_yaml.is_file():
            return True

    clogger.info('dependabot not found in any known places')
    return False


def is_dependabot_in_component_repo(
    repo: str,
    github: str,
    github_cfg
) -> bool:
    clogger = dso.util.component_logger(__name__)

    clogger.info(f'cloning {github}/{repo}')
    with tempfile.TemporaryDirectory() as temp_dir:
        gitutil.GitHelper.clone_into(
            target_directory=temp_dir,
            github_cfg=github_cfg,
            github_repo_path=repo,
        )

        return _dependabot_yaml_in_known_places(repo_dir=temp_dir)


def _get_repo_in_org(org_name: str):
    clogger = dso.util.component_logger(__name__)
    host, org = org_name.split('/')
    clogger.info(f'looking for repos from {org=} on {host=}')

    github_cfg = ccc.github.github_cfg_for_hostname(
        cfg_factory=ci.util.ctx().cfg_factory(),
        host_name=host,
    )
    github_api = ccc.github.github_api(github_cfg)
    github_org = github_api.organization(org)
    return github_org.repositories


def _scan_repos_for_dependabot(
    repos: typing.List[str],
    host: str,
) -> dso.model.DependabotCoverageReport:

    clogger = dso.util.component_logger(__name__)
    i = 1

    report: dso.model.DependabotCoverageReport
    details = []

    clogger.info(f'scanning {len(repos)} repos')
    for repo in repos:
        github_cfg = ccc.github.github_cfg_for_hostname(
            cfg_factory=ci.util.ctx().cfg_factory(),
            host_name=host,
        )
        clogger.info(f'{i} / {len(repos)}')
        dependabot_found = is_dependabot_in_component_repo(
            repo=repo,
            github_cfg=github_cfg,
            github=host
        )
        repo_report = dso.model.DependabotCoverageReportRepo(
            dependabot=dependabot_found,
            repo=repo
        )
        details.append(repo_report)
        i += 1

    return dso.model.DependabotCoverageReport(
        coverage=0,
        details=details,
        github=host,
    )


def _get_github_api(host):
    github_cfg = ccc.github.github_cfg_for_hostname(
        cfg_factory=ci.util.ctx().cfg_factory(),
        host_name=host,
    )
    return ccc.github.github_api(github_cfg)


def _get_scoped_repos_for_org(
    org: str,
) -> typing.List[str]:
    clogger = dso.util.component_logger(__name__)
    repos: typing.List[str] = []

    clogger.info(f'org specified, scanning {org}')
    host, org = org.split('/')
    github_api = _get_github_api(host)
    github_org = github_api.organization(org)
    for repo in github_org.repositories():
        repos.append(repo.full_name)

    return repos[:3]


def _generate_reporting_tables(
    report: dso.model.DependabotCoverageReport,
    tablefmt: str,
):
    # monkeypatch: disable html escaping
    tabulate.htmlescape = lambda x: x

    tables = []
    table_data = (
        (
            rr.repo,
            rr.dependabot,
        ) for rr in report.details
    )
    tables.append(tabulate.tabulate(
        headers=('Component', 'Dependabot'),
        tabular_data=table_data,
        tablefmt=tablefmt,
        colalign=('left', 'center'),
    ))

    table_data = ((report.github, f'{report.coverage}%'),)
    tables.append(tabulate.tabulate(
        headers=('Overall Coverage', 'Percentage'),
        tabular_data=table_data,
        tablefmt=tablefmt,
        colalign=('left', 'center'),
    ))
    return tables


def _print_report(
    report: dso.model.DependabotCoverageReport,
    outfile_path: str,
):
    tables = _generate_reporting_tables(report=report, tablefmt='simple')
    with open(outfile_path, 'a') as f:
        now = datetime.datetime.now()
        f.write(f'{now.strftime("%d/%m/%Y %H:%M:%S")}\n')
        for t in tables:
            f.write(f'{t}\n\n')
            print(f'\n{t}')
        f.write('\n===================================================================\n\n')
    print('\n')


def dependabot_coverage(
    org: str,
    outfile_path: str,
):
    repos = _get_scoped_repos_for_org(org=org)
    host, _ = org.split('/')
    report = _scan_repos_for_dependabot(repos=repos, host=host)
    report.calculate_overall_percentage()
    _print_report(report=report, outfile_path=outfile_path)
