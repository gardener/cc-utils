import logging
import reutil

import ccc.github
import checkmarx.util
import cnudie.retrieve
import concourse.steps.scan_sources

__cmd_name__ = 'cx'

logger = logging.getLogger(__name__)


def scan(
    checkmarx_cfg_name: str,
    component: str,
    scan_timeout: int=3600,
    team_id: str=None,
    force: bool=False,
    exclude_regex: [str] = [],
    include_regex: [str] = [],
):
    lookup = cnudie.retrieve.create_default_component_descriptor_lookup()
    component_descriptor = lookup(component)

    concourse.steps.scan_sources.scan_sources(
        checkmarx_cfg_name=checkmarx_cfg_name,
        team_id=team_id,
        component_descriptor=component_descriptor,
        force=force,
        exclude_paths=exclude_regex,
        include_paths=include_regex,
        timeout_seconds=scan_timeout,
    )


def generate_scan_archive(
    repo_url: str, # github.com/gardener/cc-utils
    commit_hash: str | None=None,
    exclude_regex: [str] = [],
    include_regex: [str] = [],
    out_file_path: str='checkmarx_archive',
):
    '''
    create checkmarx scan archive with filters applied
    does not upload to scan API

    commit_hash defaults to repo main branch
    '''

    path_filter_func = reutil.re_filter(
        include_regexes=include_regex,
        exclude_regexes=exclude_regex,
    )

    gh_api = ccc.github.github_api(repo_url=repo_url)
    _, org, repo = repo_url.split('/')
    repo = gh_api.repository(org, repo)

    if not commit_hash:
        commit_hash = repo.default_branch

    logger.info(f'{commit_hash=}')
    logger.info(f'{out_file_path=}')

    with open(out_file_path, 'wb') as f:
        checkmarx.util._download_and_zip_repo(
            clogger=logger,
            repo=repo,
            ref=commit_hash,
            tmp_file=f,
            path_filter_func=path_filter_func,
        )
        f.seek(0)
