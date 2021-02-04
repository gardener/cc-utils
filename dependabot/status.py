import datetime
import github3.repos
import logging
import tabulate
import typing
from urllib.parse import (
    urljoin,
    urlparse,
)

import ci.util
from dependabot.model import (
    DependebotStatus,
    DependabotStatusForRepo,
)
import dependabot.util

logger = logging.getLogger(__name__)


def _retrieve_known_dependabot_file(
    repository: github3.repos.repo.ShortRepository,
):

    # According to https://docs.github.com/en/github/administering-a-repository/configuration-options
    # -for-dependency-updates , there is only one valid path for the dependabot.yml.
    try:
        file = repository.file_contents(path='.github/dependabot.yml')
        ci.util.info(f'dependabot in {repository.full_name}')
        return file
    except github3.exceptions.NotFoundError:
        return None


def _validate_dependabot_file(
    dependabot_file,
) -> bool:

    # TODO to be extended, e.g. scan for required attributes, see:
    # https://docs.github.com/en/github/administering-a-repository/
    # configuration-options-for-dependency-updates
    content = dependabot_file.decoded.decode('utf-8')
    try:
        ci.util.load_yaml(content)
    except AttributeError:
        return False
    return True


def dependabot_status(
    repository: github3.repos.repo.ShortRepository,
) -> DependabotStatusForRepo:

    if not (dependabot_file := _retrieve_known_dependabot_file(repository)):
        return DependabotStatusForRepo(
            repo=repository,
            status=DependebotStatus.NOT_ENABLED,
        )

    if _validate_dependabot_file(dependabot_file):
        return DependabotStatusForRepo(
            repo=repository,
            status=DependebotStatus.ENABLED,
        )
    else:
        ci.util.warning(f'dependabot in {dependabot_file} but validation failed')
        return DependabotStatusForRepo(
            repo=repository,
            status=DependebotStatus.UNKNOWN,
        )


def _generate_report_tables_from_repo_status(
    repo_status: typing.List[DependabotStatusForRepo],
    github_hostname: str,
    org: str,
):

    tables = []
    table_data = (
        (
            rs.repo,
            rs.status,
        ) for rs in repo_status
    )
    tables.append(tabulate.tabulate(
        headers=('Component', 'Dependabot'),
        tabular_data=table_data,
        tablefmt='simple',
        colalign=('left', 'center'),
    ))

    table_data = (
        (
            urljoin(github_hostname, org),
            f'{_calculate_coverage_percentage(repo_status=repo_status)}%'
        ),
    )
    tables.append(tabulate.tabulate(
        headers=('Full org name', 'Coverage in percentage'),
        tabular_data=table_data,
        tablefmt='simple',
        colalign=('left', 'center'),
    ))
    return tables


def _print_report_from_repo_status(
    repo_status: typing.List[DependabotStatusForRepo],
    outfile_path: str,
    github_hostname: str,
    org: str,
):

    report_tables = _generate_report_tables_from_repo_status(
        repo_status=repo_status,
        github_hostname=github_hostname,
        org=org,
    )

    with open(outfile_path, 'a') as f:
        now = datetime.datetime.now()
        f.write(f'{now.isoformat()}\n')
        for t in report_tables:
            f.write(f'{t}\n\n')
            print(f'\n{t}')
        f.write(f'\n{"=" * 20}\n\n')
    print('\n')


def _calculate_coverage_percentage(
    repo_status: typing.List[DependabotStatusForRepo],
) -> float:

    t = 0
    for e in repo_status:
        if e.status == DependebotStatus.ENABLED:
            t += 1

    try:
        return round(t / len(repo_status), 2)
    except ZeroDivisionError:
        return 0


def status_for_org(
    github_hostname: str,
    org: str,
    outfile_path: str,
):

    if '://' not in github_hostname:
        github_hostname = 'x://' + github_hostname

    # strip scheme (protocol) from github hostname
    parsed_url = urlparse(github_hostname)
    github_hostname = parsed_url.hostname

    repos = dependabot.util.repositories_for_org(
        github_hostname=github_hostname,
        org=org,
    )
    repo_status = [status for repo in repos for status in [dependabot_status(repo)]]
    _print_report_from_repo_status(
        repo_status=repo_status,
        outfile_path=outfile_path,
        github_hostname=github_hostname,
        org=org,
    )
