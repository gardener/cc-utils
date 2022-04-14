import dataclasses
import requests
import typing

import gci.componentmodel as cm

import ci.util
import cnudie.util
import dso.model


class DeliveryServiceRoutes:
    def __init__(self, base_url: str):
        self._base_url = base_url

    def component_descriptor(self):
        return ci.util.urljoin(
            self._base_url,
            'cnudie',
            'component',
        )

    def upload_metadata(self):
        return ci.util.urljoin(
            self._base_url,
            'artefacts',
            'upload-metadata',
        )

    def component_responsibles(self):
        return ci.util.urljoin(
            self._base_url,
            'cnudie',
            'component',
            'responsibles',
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
        data: dso.model.ComplianceData,
    ):
        res = requests.post(
            url=self._routes.upload_metadata(),
            json={'entries': [dataclasses.asdict(data)]},
        )

        res.raise_for_status()

    def component_responsibles(
        self,
        name: str=None,
        version: str=None,
        ctx_repo_url: str=None,
        component: typing.Union[cm.Component, cm.ComponentDescriptor]=None,
    ) -> dict:
        '''
        retrieves component-responsibles. Responsibles are returned as a list of typed user
        identities.

        known types: githubUser, emailAddress, personalName
        example (single user entry): [
            {type: githubUser, username: <username>, source: <url>},
            {type: emailAddress, email: <email-addr>, source: <url>},
            {type: peronalName, firstName, lastName, source: <url>},
        ]
        '''

        if any((name, version, ctx_repo_url)):
            if not all((name, version, ctx_repo_url)):
                raise ValueError('either all or not of name, version, ctx_repo_url must be set')
            elif component:
                raise ValueError('must pass either name, version, ctx_repo_url, OR component')
        elif component := cnudie.util.to_component(component):
            name = component.name
            version = component.version
            ctx_repo_url = component.current_repository_ctx().baseUrl
        else:
            raise ValueError('must either pass component or name, version ctx_repo_url')

        url = self._routes.component_responsibles()

        resp = requests.get(
            url=url,
            params={
                'component_name': name,
                'version': version,
                'ctx_repo_url': ctx_repo_url,
            }
        )

        resp.raise_for_status()

        return resp.json()['responsibles']


def github_users_from_responsibles(
    responsibles: typing.Iterable[dict],
) -> typing.Generator[str, None, None]:
    '''
    returns a generator yielding all github-usernames from the given `responsibles`.
    use `DeliveryServiceClient.component_responsibles` to retrieve responsibles
    '''
    for responsible in responsibles:
        for responsible_info in responsible:
            if not responsible_info['type'] == 'githubUser':
                continue
            yield responsible_info['username']
