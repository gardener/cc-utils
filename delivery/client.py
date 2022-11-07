import dataclasses
import datetime
import logging
import requests
import typing

import gci.componentmodel as cm

import ci.util
import cnudie.util
import delivery.model as dm
import dso.model


logger = logging.getLogger(__name__)


class DeliveryServiceRoutes:
    def __init__(self, base_url: str):
        self._base_url = base_url

    def component_descriptor(self):
        return ci.util.urljoin(
            self._base_url,
            'cnudie',
            'component',
        )

    def component_responsibles(self):
        return ci.util.urljoin(
            self._base_url,
            'cnudie',
            'component',
            'responsibles',
        )

    def _delivery(self, *suffix: typing.Iterable[str]):
        return ci.util.urljoin(
            self._base_url,
            'delivery',
            *suffix,
        )

    def sprint_infos(self):
        return self._delivery('sprint-infos')

    def sprint_current(self):
        return self._delivery('sprint-infos', 'current')

    def upload_metadata(self):
        return ci.util.urljoin(
            self._base_url,
            'artefacts',
            'upload-metadata',
        )

    def query_metadata(self):
        return ci.util.urljoin(
            self._base_url,
            'artefacts',
            'query-metadata',
        )

    def os_branches(self, os_id: str):
        return ci.util.urljoin(
            self._base_url,
            'os',
            os_id,
            'branches',
        )


class DeliveryServiceClient:
    def __init__(
        self,
        routes: DeliveryServiceRoutes,
    ):
        self._routes = routes

    def component_descriptor(
        self,
        name: str,
        version: str,
        ctx_repo_url: str,
        validation_mode: cm.ValidationMode=cm.ValidationMode.NONE,
    ):
        res = requests.get(
            url=self._routes.component_descriptor(),
            params={
                'component_name': name,
                'version': version,
                'ctx_repo_url': ctx_repo_url,
            },
        )

        res.raise_for_status()

        return cm.ComponentDescriptor.from_dict(
            res.json(),
            validation_mode=validation_mode,
        )

    def upload_metadata(
        self,
        data: dso.model.ArtefactMetadata,
    ):
        res = requests.post(
            url=self._routes.upload_metadata(),
            json={'entries': [
                    dataclasses.asdict(
                        data,
                        dict_factory=ci.util.dict_to_json_factory
                    )
                ]
            },
        )

        res.raise_for_status()

    def component_responsibles(
        self,
        name: str=None,
        version: str=None,
        ctx_repo_url: str=None,
        component: typing.Union[cm.Component, cm.ComponentDescriptor]=None,
        artifact: typing.Union[cm.Artifact, str]=None,
    ) -> dict:
        '''
        retrieves component-responsibles. Responsibles are returned as a list of typed user
        identities. Optionally, an artifact (or artifact name) may be passed. In this case,
        responsibles are filtered for the given resource definition. Note that an error will
        be raised if the given artifact does not declare a artifact of the given name.

        known types: githubUser, emailAddress, personalName
        example (single user entry): [
            {type: githubUser, username: <username>, source: <url>, github_hostname: <hostname>},
            {type: emailAddress, email: <email-addr>, source: <url>},
            {type: peronalName, firstName, lastName, source: <url>},
        ]
        '''

        if any((name, version, ctx_repo_url)):
            if not all((name, version, ctx_repo_url)):
                raise ValueError('either all or not of name, version, ctx_repo_url must be set')
            elif component:
                raise ValueError('must pass either name, version, ctx_repo_url, OR component')
        elif component and (component := cnudie.util.to_component(component)):
            name = component.name
            version = component.version
            ctx_repo_url = component.current_repository_ctx().baseUrl
        else:
            raise ValueError('must either pass component or name, version ctx_repo_url')

        url = self._routes.component_responsibles()

        params = {
            'component_name': name,
            'version': version,
            'ctx_repo_url': ctx_repo_url,
        }

        if artifact:
            if isinstance(artifact, cm.Artifact):
                artifact_name = artifact.name
            else:
                artifact_name = artifact

            params['artifact_name'] = artifact_name

        logger.info(f'{component.identity()=} {params=}')

        resp = requests.get(
            url=url,
            params=params,
        )

        resp.raise_for_status()

        return resp.json()['responsibles']

    def sprints(self) -> list[dm.Sprint]:
        raw = requests.get(
            url=self._routes.sprint_infos(),
        ).json()['sprints']

        return [
            dm.Sprint.from_dict(sprint_info)
            for sprint_info in raw
        ]

    def sprint_current(self, offset: int=0, before: datetime.date=None) -> dm.Sprint:
        extra_args = {}
        if before:
            if isinstance(before, datetime.date) or isinstance(before, datetime.date):
                extra_args['before'] = before.isoformat()
            else:
                extra_args['before'] = before

        resp = requests.get(
            url=self._routes.sprint_current(),
            params={'offset': offset, **extra_args},
        )

        resp.raise_for_status()

        return dm.Sprint.from_dict(resp.json())

    def query_metadata_raw(self, components: typing.Iterable[cm.Component]):
        query = {
            'components': [
                {
                    'componentName': c.name,
                    'componentVersion': c.version,
                } for c in components
            ]
        }

        res = requests.post(
            url=self._routes.query_metadata(),
            json=query,
        )

        return res.json()

    def os_release_infos(self, os_id: str, absent_ok=False) -> list[dm.OsReleaseInfo]:
        url = self._routes.os_branches(os_id=os_id)

        res = requests.get(url)

        if not absent_ok:
            res.raise_for_status()
        elif not res.ok:
            return None

        return [
            dm.OsReleaseInfo.from_dict(ri) for ri in res.json()
        ]


def _normalise_github_hostname(github_url: str):
    # hack: for github.com, we might get a different subdomain (api.github.com)
    github_hostname = ci.util.urlparse(github_url).hostname
    parts = github_hostname.strip('.').split('.')
    if parts[0] == 'api':
        parts = parts[1:]
    github_hostname = '.'.join(parts)

    return github_hostname.lower()


def github_users_from_responsibles(
    responsibles: typing.Iterable[dict],
    github_url: str=None,
) -> typing.Generator[dm.GithubUser, None, None]:
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

            yield dm.GithubUser(username=username, github_hostname=github_hostname)
