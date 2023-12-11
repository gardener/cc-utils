import dataclasses
import datetime
import logging
import requests
import typing

import dacite

import gci.componentmodel as cm

import ci.util
import cnudie.iter
import cnudie.retrieve
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

    def greatest_component_versions(self):
        return ci.util.urljoin(
            self._base_url,
            'cnudie',
            'component',
            'versions',
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

    def update_metadata(self):
        return ci.util.urljoin(
            self._base_url,
            'artefacts',
            'update-metadata',
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

    def components_metadata(self):
        return ci.util.urljoin(
            self._base_url,
            'components',
            'metadata',
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
        ctx_repo_url: str=None,
        validation_mode: cm.ValidationMode=cm.ValidationMode.NONE,
    ):
        params = {
            'component_name': name,
            'version': version,
        }
        if ctx_repo_url:
            params['ctx_repo_url'] = ctx_repo_url

        res = requests.get(
            url=self._routes.component_descriptor(),
            params=params,
        )

        res.raise_for_status()

        return cm.ComponentDescriptor.from_dict(
            res.json(),
            validation_mode=validation_mode,
        )

    def greatest_component_versions(
        self,
        component_name: str,
        max_versions: int=5,
        greatest_version: str=None,
        ocm_repo: cm.OcmRepository=None,
        ignore_prerelease_versions: bool | None=None,
    ):
        params = {
            'component_name': component_name,
            'max': max_versions,
        }
        if greatest_version:
            params['version'] = greatest_version
        if ocm_repo:
            if not isinstance(ocm_repo, cm.OciOcmRepository):
                raise NotImplementedError(ocm_repo)
            params['ocm_repo_url'] = ocm_repo.baseUrl
            params['ctx_repo_url'] = ocm_repo.baseUrl # TODO remove once updated in delivery-service
        if ignore_prerelease_versions is not None:
            params['ignore_prerelease_versions'] = ignore_prerelease_versions

        res = requests.get(
            url=self._routes.greatest_component_versions(),
            params=params,
        )

        res.raise_for_status()

        return res.json()

    def upload_metadata(
        self,
        data: typing.Iterable[dso.model.ArtefactMetadata],
    ):
        res = requests.post(
            url=self._routes.upload_metadata(),
            json={'entries': [
                dataclasses.asdict(
                    artefact_metadata,
                    dict_factory=ci.util.dict_to_json_factory,
                ) for artefact_metadata in data
            ]},
        )

        res.raise_for_status()

    def update_metadata(
        self,
        data: typing.Iterable[dso.model.ArtefactMetadata],
    ):
        res = requests.put(
            url=self._routes.update_metadata(),
            json={'entries': [
                dataclasses.asdict(
                    artefact_metadata,
                    dict_factory=ci.util.dict_to_json_factory,
                ) for artefact_metadata in data
            ]},
        )

        res.raise_for_status()

    def component_responsibles(
        self,
        name: str=None,
        version: str=None,
        ctx_repo_url: str=None,
        component: typing.Union[cm.Component, cm.ComponentDescriptor]=None,
        artifact: typing.Union[cm.Artifact, str]=None,
    ) -> tuple[dict, list[dm.Status]]:
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

        if any((name, version, ctx_repo_url)):
            if not all((name, version)):
                raise ValueError('either all or none of name and version must be set')
            elif component:
                raise ValueError('must pass either name, version (and ctx_repo_url) OR component')
        elif component and (component := cnudie.util.to_component(component)):
            name = component.name
            version = component.version
        else:
            raise ValueError('must either pass component or name, version (and ctx_repo_url)')

        url = self._routes.component_responsibles()

        params = {
            'component_name': name,
            'version': version,
        }
        if ctx_repo_url:
            params['ctx_repo_url'] = ctx_repo_url

        if artifact:
            if isinstance(artifact, cm.Artifact):
                artifact_name = artifact.name
            else:
                artifact_name = artifact

            params['artifact_name'] = artifact_name

        if component:
            logger.info(f'{component.identity()=} {params=}')
        else:
            logger.info(f'{params=}')

        resp = requests.get(
            url=url,
            params=params,
        )

        resp.raise_for_status()
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

    def query_metadata_raw(
        self,
        components: typing.Iterable[cm.Component],
        type: dso.model.Datatype=None,
    ):
        if type:
            params = {
                'type': type,
            }
        else:
            params = dict()

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
            params=params,
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

    def components_metadata(
        self,
        component_name: str,
        component_version: str=None,
        metadata_types: list[str]=[], # empty list returns _all_ metadata-types
        select: str=None, # either `greatestVersion` or `latestDate`
    ) -> list[dm.ArtefactMetadata]:
        '''
        returns a list of artifact-metadata for the given component

        One of 'select' and 'component_version' must be given. However, if 'select' is given as
        `greatestVersion`, 'version' must _not_ be given.
        '''
        url = self._routes.components_metadata()

        resp = requests.get(
            url=url,
            params={
                'name': component_name,
                'version': component_version,
                'type': metadata_types,
                'select': select,
            }
        )

        resp.raise_for_status()

        return [
            dm.ArtefactMetadata.from_dict(raw)
            for raw in resp.json()
        ]

    def artefact_metadata_for_resource_node(
        self,
        resource_node: 'cnudie.iter.ResourceNode',
        types: list[str],
    ) -> typing.Iterable[dm.ArtefactMetadata]:
        '''Return an iterable that contains all stored `ArtefactMetadata` of the given type for the
        given resource node.

        For possible values for `type` see `dso.model.Datatype`.
        '''

        component = resource_node.component
        resource = resource_node.resource

        for component_metadata in self.components_metadata(
            component_name=component.name,
            metadata_types=types,
            component_version=component.version,
        ):
            if not component_metadata.artefactId.componentName == component.name:
                continue
            if not component_metadata.artefactId.artefactName == resource.name:
                continue
            if not component_metadata.artefactId.artefactVersion == resource.version:
                continue

            yield component_metadata

    def metadata(
        self,
        component: cnudie.retrieve.ComponentName=None,
        artefact: str=None,
        node: cnudie.iter.Node=None,
        types: typing.Iterable[str]=None,
    ) -> typing.Generator[dm.ArtefactMetadata, None, None]:
        if component:
            component = cnudie.util.to_component_id(component)

        if types:
            types = tuple(types)

        if not (bool(component) ^ bool(node)):
            raise ValueError('exactly one of component, node must be passed')

        if node:
            component = node.component
            artefact = node.artefact

        if isinstance(artefact, cm.Artifact):
            artefact_name = artefact.name
            artefact_version = artefact.version
        elif isinstance(artefact, str):
            artefact_name = artefact
            artefact_version = None

        for metadata in self.components_metadata(
            component_name=component.name,
            component_version=component.version,
            metadata_types=types,
        ):
            if not artefact:
                yield metadata
                continue

            # todo: also check for artefact-type + consider version is an optional attr
            #       + consider extra-id (keep it simple for now)
            artefact_id = metadata.artefactId
            if artefact_name and artefact_id.artefactName != artefact_name:
                continue
            if artefact_version and artefact_id.artefactVersion != artefact_version:
                continue
            yield metadata


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
