import collections.abc
import dataclasses
import datetime
import logging
import time
import typing

import dacite
import requests.exceptions
import requests.sessions

import ocm

import ci.util
import cnudie.util
import delivery.jwt
import delivery.model as dm
import http_requests


logger = logging.getLogger(__name__)


class DeliveryServiceRoutes:
    def __init__(self, base_url: str):
        self._base_url = base_url

    def auth(self):
        return ci.util.urljoin(
            self._base_url,
            'auth',
        )

    def auth_configs(self):
        return ci.util.urljoin(
            self._base_url,
            'auth',
            'configs',
        )

    def openid_configuration(self):
        '''
        endpoint according to OpenID provider configuration request
        https://openid.net/specs/openid-connect-discovery-1_0.html#ProviderConfigurationRequest
        '''
        return ci.util.urljoin(
            self._base_url,
            '.well-known',
            'openid-configuration',
        )

    def component_descriptor(self):
        return ci.util.urljoin(
            self._base_url,
            'ocm',
            'component',
        )

    def greatest_component_versions(self):
        return ci.util.urljoin(
            self._base_url,
            'ocm',
            'component',
            'versions',
        )

    def component_responsibles(self):
        return ci.util.urljoin(
            self._base_url,
            'ocm',
            'component',
            'responsibles',
        )

    def _delivery(self, *suffix: collections.abc.Iterable[str]):
        return ci.util.urljoin(
            self._base_url,
            'delivery',
            *suffix,
        )

    def sprint_infos(self):
        return self._delivery('sprint-infos')

    def sprint_current(self):
        return self._delivery('sprint-infos', 'current')

    def artefact_metadata(self):
        return ci.util.urljoin(
            self._base_url,
            'artefacts',
            'metadata',
        )

    def artefact_metadata_query(self):
        return ci.util.urljoin(
            self._base_url,
            'artefacts',
            'metadata',
            'query',
        )

    def cache(self):
        return ci.util.urljoin(
            self._base_url,
            'cache',
        )

    def backlog_items(self):
        return ci.util.urljoin(
            self._base_url,
            'service-extensions',
            'backlog-items',
        )


Url: typing.TypeAlias = str
AuthToken: typing.TypeAlias = str
'''
A lookup crafted slightly special-cased for auth-token-based authentication. Implementations *must*
accept a single positional parameter, which is the URL for which the lookup should return a (valid)
auth-token.
If the lookup cannot offer an authtoken for a given URL, it *must* return None. Exceptions raised
by lookups are not handled.
'''
AuthTokenLookup: typing.TypeAlias = typing.Callable[[Url], AuthToken]


class DeliveryServiceClient:
    def __init__(
        self,
        routes: DeliveryServiceRoutes,
        auth_token_lookup: AuthTokenLookup | None=None,
    ):
        '''
        Initialises a client which can be used to interact with the delivery-service.

        :param DeliveryServiceRoutes routes
            object which contains information of the base url of the desired instance of the
            delivery-service as well as the available routes
        :param AuthTokenLookup auth_token_lookup (optional)
            the lookup to use for retrieving auth-tokens against oauth-endpoints
        '''
        self._routes = routes
        self.auth_token_lookup = auth_token_lookup
        self.auth_credentials: dm.GitHubAuthCredentials = None # filled lazily as needed

        self._bearer_token = None
        self._session = requests.sessions.Session()

    def _openid_configuration(self):
        '''
        response according to OpenID provider configuration response
        https://openid.net/specs/openid-connect-discovery-1_0.html#ProviderConfigurationResponse
        '''
        res = self._session.get(
            url=self._routes.openid_configuration(),
            timeout=(4, 31),
        )

        res.raise_for_status()

        return res.json()

    def _openid_jwks(self):
        openid_configuration = self._openid_configuration()

        res = self._session.get(
            url=openid_configuration.get('jwks_uri'),
            timeout=(4, 31),
        )

        res.raise_for_status()

        return res.json()

    def _authenticate(self):
        if self._bearer_token and not delivery.jwt.is_jwt_token_expired(
            token=self._bearer_token,
            token_expiration_buffer_seconds=30,
        ):
            return

        if not self.auth_token_lookup:
            logger.info('DeliverService-Client has no auth-token-lookup - attempting anonymous auth')
            return

        if (
            self.auth_credentials
            and self.auth_credentials.auth_token.startswith('ey')
            and delivery.jwt.is_jwt_token_expired(
                token=self.auth_credentials.auth_token,
                token_expiration_buffer_seconds=30,
            )
        ):
            self.auth_credentials = None

        if not self.auth_credentials:
            res = self._session.get(
                url=self._routes.auth_configs(),
                timeout=(4, 31),
            )

            res.raise_for_status()

            auth_configs = res.json()

            for auth_config in auth_configs:
                api_url = auth_config.get('api_url')

                if (auth_token := self.auth_token_lookup(api_url)):
                    break
            else:
                logger.info('no valid credentials found - attempting anonymous-auth')
                return

            self.auth_credentials = dm.GitHubAuthCredentials(
                api_url=api_url,
                auth_token=auth_token,
            )

        params = {
            'access_token': self.auth_credentials.auth_token,
            'api_url': self.auth_credentials.api_url,
        }

        res = self._session.get(
            url=self._routes.auth(),
            params=params,
            timeout=(4, 31),
        )

        if not res.ok:
            logger.warning(
                'authentication against delivery-service failed: '
                f'{res.status_code=} {res.reason=} {res.content=}'
            )

        res.raise_for_status()

        self._bearer_token = res.cookies.get(delivery.jwt.JWT_KEY)

        if not self._bearer_token:
            raise ValueError('delivery-service returned no bearer token upon authentication')

    def request(
        self,
        url: str,
        method: str='GET',
        headers: dict=None,
        **kwargs,
    ):
        try:
            self._authenticate()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code != 400:
                raise
            if e.response.json().get('error_id') != 'feature-inactive':
                raise
            logger.info('delivery-service authentication feature is inactive')

        headers = headers or {}

        if self._bearer_token:
            headers = {
                'Authorization': f'Bearer {self._bearer_token}',
                **headers,
            }

        try:
            timeout = kwargs.pop('timeout')
        except KeyError:
            timeout = (4, 31)

        res = self._session.request(
            method=method,
            url=url,
            headers=headers,
            timeout=timeout,
            **kwargs,
        )

        return res

    def component_descriptor(
        self,
        name: str,
        version: str,
        ocm_repo_url: str=None,
        version_filter: str | None=None,
        validation_mode: ocm.ValidationMode | None=None,
    ):
        params = {
            'component_name': name,
            'version': version,
        }
        if ocm_repo_url:
            params['ocm_repo_url'] = ocm_repo_url
        if version_filter is not None:
            params['version_filter'] = version_filter

        res = self.request(
            url=self._routes.component_descriptor(),
            params=params,
        )

        res.raise_for_status()

        return ocm.ComponentDescriptor.from_dict(
            res.json(),
            validation_mode=validation_mode,
        )

    def greatest_component_versions(
        self,
        component_name: str,
        max_versions: int=5,
        greatest_version: str=None,
        ocm_repo: ocm.OcmRepository=None,
        version_filter: str | None=None,
        start_date: datetime.date=None,
        end_date: datetime.date=None,
        timeout: tuple[float, float]=(4.0, 121.0),
    ):
        params = {
            'component_name': component_name,
            'max': max_versions,
        }
        if greatest_version:
            params['version'] = greatest_version
        if ocm_repo:
            if not isinstance(ocm_repo, ocm.OciOcmRepository):
                raise NotImplementedError(ocm_repo)
            params['ocm_repo_url'] = ocm_repo.oci_ref
        if version_filter is not None:
            params['version_filter'] = version_filter

        if start_date:
            params['start_date'] = start_date.isoformat()

        if end_date:
            params['end_date'] = end_date.isoformat()

        res = self.request(
            url=self._routes.greatest_component_versions(),
            params=params,
            timeout=timeout,
        )

        res.raise_for_status()

        return res.json()

    def update_metadata(
        self,
        data: collections.abc.Iterable[typing.Union[dict, 'ArtefactMetadata']],
    ):
        headers = {
            'Content-Type': 'application/json',
        }

        data, headers = http_requests.encode_request(
            json={'entries': [
                dataclasses.asdict(
                    artefact_metadata,
                    dict_factory=ci.util.dict_to_json_factory,
                ) if dataclasses.is_dataclass(artefact_metadata) else artefact_metadata
                for artefact_metadata in data
            ]},
            headers=headers,
        )

        res = self.request(
            url=self._routes.artefact_metadata(),
            method='PUT',
            headers=headers,
            data=data,
            timeout=None,
        )

        res.raise_for_status()

    def delete_metadata(
        self,
        data: collections.abc.Iterable[typing.Union[dict, 'ArtefactMetadata']],
    ):
        headers = {
            'Content-Type': 'application/json',
        }

        data, headers = http_requests.encode_request(
            json={'entries': [
                dataclasses.asdict(
                    artefact_metadata,
                    dict_factory=ci.util.dict_to_json_factory,
                ) if dataclasses.is_dataclass(artefact_metadata) else artefact_metadata
                for artefact_metadata in data
            ]},
            headers=headers,
        )

        res = self.request(
            url=self._routes.artefact_metadata(),
            method='DELETE',
            headers=headers,
            data=data,
            timeout=None,
        )

        res.raise_for_status()

    def component_responsibles(
        self,
        name: str=None,
        version: str=None,
        ocm_repo_url: str=None,
        version_filter: str | None=None,
        component: ocm.Component | ocm.ComponentDescriptor=None,
        artifact: ocm.Artifact | str=None,
        absent_ok: bool=False,
    ) -> tuple[list[dict] | None, list[dm.Status] | None]:
        '''
        retrieves component-responsibles and optional status info.
        Status info can be used to communicate additional information, e.g. that responsible-label
        was malformed.
        Responsibles are returned as a list of typed user identities. Optionally, an artifact
        (or artifact name) may be passed. In this case, responsibles are filtered for the given
        resource definition. Note that an error will be raised if the given artifact does not declare
        a artifact of the given name.

        known types: githubUser, emailAddress, personalName
        example (single user entry): [
            {type: githubUser, username: <username>, source: <url>, github_hostname: <hostname>},
            {type: emailAddress, email: <email-addr>, source: <url>},
            {type: peronalName, firstName, lastName, source: <url>},
        ]
        '''

        if any((name, version, ocm_repo_url)):
            if component:
                raise ValueError('must pass either name (and version and ocm_repo_url) OR component')
        elif component and (component := cnudie.util.to_component(component)):
            name = component.name
            version = component.version
        else:
            raise ValueError('must either pass component or name, version (and ocm_repo_url)')

        url = self._routes.component_responsibles()

        params = {
            'component_name': name,
        }
        if version:
            params['version'] = version
        if ocm_repo_url:
            params['ocm_repo_url'] = ocm_repo_url
        if version_filter is not None:
            params['version_filter'] = version_filter

        if artifact:
            if isinstance(artifact, ocm.Artifact):
                artifact_name = artifact.name
            else:
                artifact_name = artifact

            params['artifact_name'] = artifact_name

        if component:
            logger.info(f'{component.identity()=} {params=}')
        else:
            logger.info(f'{params=}')

        # wait for responsibles result
        # -> delivery service is waiting up to ~2 min for contributor statistics
        for _ in range(24):
            resp = self.request(
                url=url,
                params=params,
                timeout=(4, 121),
            )
            if resp.status_code != 202:
                break
            time.sleep(5)

        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404 and absent_ok:
                logger.warning(f'delivery service returned 404 for responsibles with {params=}')
                return None, None
            raise

        resp_json: dict = resp.json()

        responsibles = resp_json['responsibles']
        statuses_raw = resp_json.get('statuses', [])
        statuses = [
            dacite.from_dict(
                data_class=dm.Status,
                data=status_raw,
                config=dacite.Config(
                    cast=[
                        dm.StatusType,
                    ],
                ),
            )
            for status_raw in statuses_raw
        ]

        return responsibles, statuses

    def sprints(self) -> list[dm.Sprint]:
        resp = self.request(
            url=self._routes.sprint_infos(),
        )

        resp.raise_for_status()

        sprints_raw = resp.json()['sprints']

        return [
            dm.Sprint.from_dict(sprint_info)
            for sprint_info in sprints_raw
        ]

    def sprint_current(self, offset: int=0, before: datetime.date=None) -> dm.Sprint:
        extra_args = {}
        if before:
            if isinstance(before, datetime.date) or isinstance(before, datetime.date):
                extra_args['before'] = before.isoformat()
            else:
                extra_args['before'] = before

        resp = self.request(
            url=self._routes.sprint_current(),
            params={'offset': offset, **extra_args},
        )

        resp.raise_for_status()

        return dm.Sprint.from_dict(resp.json())

    def query_metadata(
        self,
        components: collections.abc.Iterable[ocm.Component]=(),
        artefacts: collections.abc.Iterable[typing.Union[dict, 'ComponentArtefactId']]=(),
        type: str | collections.abc.Sequence[str]=None,
        referenced_type: str | collections.abc.Sequence[str]=None,
    ) -> tuple[dict]:
        '''
        Query artefact metadata from the delivery-db.

        @param components:      component identities used for filtering; if no identities are
                                specified, no component filtering is done
        @param type:            datatype(s) used for filtering; if no datatype(s) is (are)
                                specified, no datatype filtering is done
        @param referenced_type: referenced datatype(s) used for filtering (only applies to artefact
                                metadata of type `rescorings`); if no datatype(s) is (are)
                                specified, no referenced datatype filtering is done
        '''
        if components and artefacts:
            raise ValueError('at most one of `artefacts` or `components` must be specified')

        params = dict()

        if type:
            params['type'] = type

        if referenced_type:
            params['referenced_type'] = referenced_type

        headers = {
            'Content-Type': 'application/json',
        }

        if components:
            entries = [
                {
                    'component_name': c.name,
                    'component_version': c.version,
                } for c in components
            ]
        else:
            entries = [
                dataclasses.asdict(artefact) if dataclasses.is_dataclass(artefact) else artefact
                for artefact in artefacts
            ]

        data, headers = http_requests.encode_request(
            json={'entries': entries},
            headers=headers,
        )

        res = self.request(
            url=self._routes.artefact_metadata_query(),
            method='POST',
            headers=headers,
            data=data,
            params=params,
            timeout=None,
        )

        res.raise_for_status()

        artefact_metadata_raw = res.json()

        return tuple(artefact_metadata_raw)

    def mark_cache_for_deletion(
        self,
        id: str | None=None,
        descriptor: dict | None=None,
        delete_after: datetime.datetime | None=None,
    ):
        if not id and not descriptor:
            raise ValueError('either `id` or `descriptor` must be specified')

        params = dict()

        if id:
            params['id'] = id

        if delete_after:
            params['deleteAfter'] = delete_after.isoformat()

        res = self.request(
            url=self._routes.cache(),
            method='DELETE',
            params=params,
            json=descriptor,
        )

        res.raise_for_status()

    def create_backlog_item(
        self,
        service: str,
        artefacts: collections.abc.Iterable[typing.Union[dict, 'ComponentArtefactId']]=(),
        priority: str | None=None, # see delivery-service k8s/backlog for allowed priorities
    ):
        headers = {
            'Content-Type': 'application/json',
        }

        params = {
            'service': service,
        }

        if priority:
            params['priority'] = priority

        data, headers = http_requests.encode_request(
            json={'artefacts': [
                dataclasses.asdict(artefact) if dataclasses.is_dataclass(artefact) else artefact
                for artefact in artefacts
            ]},
            headers=headers,
        )

        res = self.request(
            url=self._routes.backlog_items(),
            method='POST',
            headers=headers,
            data=data,
            params=params,
        )
        res.raise_for_status()


def _normalise_github_hostname(github_url: str):
    # hack: for github.com, we might get a different subdomain (api.github.com)
    github_hostname = ci.util.urlparse(github_url).hostname
    parts = github_hostname.strip('.').split('.')
    if parts[0] == 'api':
        parts = parts[1:]
    github_hostname = '.'.join(parts)

    return github_hostname.lower()


def github_usernames_from_responsibles(
    responsibles: collections.abc.Iterable[dict],
    github_url: str=None,
) -> collections.abc.Generator[str, None, None]:
    '''
    returns a generator yielding all github-users from the given `responsibles`.
    use `DeliveryServiceClient.component_responsibles` to retrieve responsibles
    if github_url is given, only github-users on a matching github-host are returned.
    This is useful if the returned users should exist on a certain target github-instance.
    github_url is gracefully parsed down to relevant hostname. It is okay to pass-in, e.g.
    a repository- or github-user-URL for convenience.
    '''
    if github_url:
        target_github_hostname = _normalise_github_hostname(github_url)
    else:
        target_github_hostname = None

    for responsible in responsibles:
        for responsible_info in responsible:
            if not responsible_info['type'] == 'githubUser':
                continue
            username = responsible_info['username']
            github_hostname = _normalise_github_hostname(responsible_info['github_hostname'])

            if target_github_hostname and target_github_hostname != github_hostname:
                continue

            yield username
