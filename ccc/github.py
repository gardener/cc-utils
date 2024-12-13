# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import datetime
import enum
import functools
import logging
import urllib.parse

import cachecontrol
import ocm
import github3
import github3.github
import github3.session

import ci.util
import github.util
import http_requests
import model
import model.github
import model.base

logger = logging.getLogger(__name__)


class SessionAdapter(enum.Enum):
    NONE = None
    RETRY = 'retry'
    CACHE = 'cache'


def github_api_ctor(
    github_cfg: model.github.GithubConfig,
    github_username: str,
    verify_ssl: bool=True,
    session_adapter: SessionAdapter=SessionAdapter.RETRY,
    cfg_factory: model.ConfigFactory=None,
):
    '''returns the appropriate github3.GitHub constructor for the given github URL

    In case github_url does not refer to github.com, the c'tor for GithubEnterprise is
    returned with the url argument preset, thus disburdening users to differentiate
    between github.com and non-github.com cases.

    github-api requests will be logged to Elasticsearch if a default Elasticsearch config is found.
    '''
    github_url = github_cfg.http_url()

    parsed = urllib.parse.urlparse(github_url)
    if parsed.scheme:
        hostname = parsed.hostname
    else:
        raise ValueError('failed to parse url: ' + str(github_url))

    session = github3.session.GitHubSession()
    original_request = session.request

    def intercepted_request(
        method: str,
        url: str,
        **kwargs,
    ):
        req = original_request(method, url, **kwargs)

        try:
            es_client.store_document(
                index='github_request',
                body={
                    'method': method,
                    'url': url,
                    'data': kwargs,
                    'creation_date': datetime.datetime.now().isoformat(),
                    'github_cfg': github_cfg.name(),
                    'github_hostname': hostname,
                    'github_username': github_username,
                },
            )
        except:
            logger.debug('unable to log github api request to Elasticsearch, '
                'will disable logging for future requests')
            session.request = original_request

        return req

    try:
        import ccc.elasticsearch
        es_client = ccc.elasticsearch.default_client_if_available(cfg_factory)
        if es_client:
            logger.debug('logging github api requests to elasticsearch')
            session.request = intercepted_request

    except Exception as e:
        logger.debug(e)
        logger.debug('unable to create elasticsearch client, will not log github-api requests')

    session_adapter = SessionAdapter(session_adapter)
    if session_adapter is SessionAdapter.NONE or not session_adapter:
        pass
    elif session_adapter is SessionAdapter.RETRY:
        session = http_requests.mount_default_adapter(
            session=session,
            flags=http_requests.AdapterFlag.RETRY,
            max_pool_size=16, # increase with care, might cause github api "secondary-rate-limit"
        )
    elif session_adapter is SessionAdapter.CACHE:
        session = cachecontrol.CacheControl(
            session,
            cache_etags=True,
        )
    else:
        raise NotImplementedError

    if hostname.lower() == 'github.com':
        return functools.partial(
            github3.github.GitHub,
            session=session,
        )
    else:
        return functools.partial(
            github3.github.GitHubEnterprise,
            url=github_url,
            verify=verify_ssl,
            session=session,
        )


def repo_helper(
    host: str,
    org: str,
    repo: str,
    branch: str='master',
    session_adapter: SessionAdapter=SessionAdapter.RETRY,
):
    api = github_api(
        github_cfg=github_cfg_for_repo_url(repo_url=ci.util.urljoin(host, org, repo)),
        session_adapter=session_adapter,
    )

    return github.util.GitHubRepositoryHelper(
        owner=org,
        name=repo,
        github_api=api,
        default_branch=branch,
    )


def pr_helper(
    host: str,
    org: str,
    repo: str,
    session_adapter: SessionAdapter=SessionAdapter.RETRY,
):
    api = github_api(
        github_cfg=github_cfg_for_repo_url(repo_url=ci.util.urljoin(host, org, repo)),
        session_adapter=session_adapter,
    )

    return github.util.PullRequestUtil(
        owner=org,
        name=repo,
        github_api=api,
    )


def github_api(
    github_cfg: model.github.GithubConfig=None,
    repo_url: str=None,
    session_adapter: SessionAdapter=SessionAdapter.RETRY,
    cfg_factory=None,
    username: str | None=None,
):
    if not (bool(github_cfg) ^ bool(repo_url)):
        raise ValueError('exactly one of github_cfg, repo_url must be passed')

    if not cfg_factory:
        try:
            cfg_factory = ci.util.ctx().cfg_factory()
        except Exception as e:
            logger.warning(f'error trying to retrieve {repo_url=} {github_cfg=}: {e}')
            raise

    if isinstance(github_cfg, str):
        github_cfg = cfg_factory().github(github_cfg)

    if repo_url:
        github_cfg = github_cfg_for_repo_url(
            repo_url=repo_url,
            cfg_factory=cfg_factory,
        )

    if username:
        github_credentials = github_cfg.credentials(username)
    else:
        github_credentials = github_cfg.credentials_with_most_remaining_quota()

    github_auth_token = github_credentials.auth_token()
    github_username = github_credentials.username()

    verify_ssl = github_cfg.tls_validation()

    github_ctor = github_api_ctor(
        github_cfg=github_cfg,
        github_username=github_username,
        verify_ssl=verify_ssl,
        session_adapter=session_adapter,
        cfg_factory=cfg_factory,
    )
    github_api = github_ctor(
        token=github_auth_token,
    )

    if not github_api:
        ci.util.fail(f'Could not connect to GitHub-instance {github_cfg.http_url()}')

    if not 'github.com' in github_cfg.api_url():
        github_api._github_url = github_cfg.api_url()

    return github_api


def github_api_lookup(repo_url, /) -> github3.GitHub:
    return github_api(repo_url=repo_url)


@functools.lru_cache()
def github_cfg_for_repo_url(
    repo_url: str | urllib.parse.ParseResult=None,
    api_url: str=None,
    cfg_factory=None,
    require_labels: tuple[str]=('ci',), # XXX unhardcode label
    github_cfgs: tuple[model.github.GithubConfig]=(),
) -> model.github.GithubConfig | None:
    if not (bool(repo_url) ^ bool(api_url)):
        raise ValueError('exactly one of `repo_url` or `api_url` must be passed')

    if isinstance(repo_url, urllib.parse.ParseResult):
        repo_url = repo_url.geturl()

    if not github_cfgs:
        if not cfg_factory:
            cfg_factory = ci.util.ctx().cfg_factory()

        github_cfgs = cfg_factory._cfg_elements(cfg_type_name='github')

    matching_cfgs = []
    for github_cfg in github_cfgs:
        if require_labels:
            missing_labels = set(require_labels) - set(github_cfg.purpose_labels())
            if missing_labels:
                # if not all required labels are present skip this element
                continue

        if (
            (repo_url and github_cfg.matches_repo_url(repo_url=repo_url))
            or (api_url and github_cfg.matches_api_url(api_url=api_url))
        ):
            matching_cfgs.append(github_cfg)

    # prefer config with most configured repo urls
    matching_cfgs = sorted(matching_cfgs, key=lambda config: len(config.repo_urls()))
    url = repo_url or api_url
    if len(matching_cfgs) == 0:
        raise model.base.ConfigElementNotFoundError(f'No github cfg found for {url=}')

    gh_cfg = matching_cfgs[-1]
    logger.debug(f'using {gh_cfg.name()=} for {url=}')
    return gh_cfg


def github_api_from_gh_access(
    access: ocm.GithubAccess,
) -> github3.github.GitHub | github3.github.GitHubEnterprise:
    if access.type is not ocm.AccessType.GITHUB:
        raise ValueError(f'{access=}')

    github_cfg = github_cfg_for_repo_url(repo_url=access.repoUrl)
    return github_api(github_cfg=github_cfg)
