import dataclasses
import requests
import typing
import urllib.parse

import gci.componentmodel as cm

import ci.util
import cnudie.util
import dso.model


@dataclasses.dataclass # TODO: deduplicate w/ modelclass in delivery-service
class GithubUser:
    username: str
    github_hostname: str


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
        resource: typing.Union[cm.Resource, str]=None,
    ) -> dict:
        '''
        retrieves component-responsibles. Responsibles are returned as a list of typed user
        identities. Optionally, a resource (or resource name) may be passed. In this case,
        responsibles are filtered for the given resource definition. Note that an error will
        be raised if the given resource does not declare a resource of the given name.

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

        if resource:
            if isinstance(resource, cm.Resource):
                resource_name = resource.name
            else:
                resource_name = resource

            params['resource_name'] = resource_name

        resp = requests.get(
            url=url,
            params=params,
        )

        resp.raise_for_status()

        return resp.json()['responsibles']


def _normalise_github_hostname(github_url: str):
    # hack: for github.com, we might get a different subdomain (api.github.com)
    if not '://' in github_url:
        github_url = 'x://' + github_url
    github_hostname = urllib.parse.urlparse(github_url).hostname
    parts = github_hostname.strip('.').split('.')
    if parts[0] == 'api':
        parts = parts[1:]
    github_hostname = '.'.join(parts)

    return github_hostname.lower()


def github_users_from_responsibles(
    responsibles: typing.Iterable[dict],
    github_url: str=None,
) -> typing.Generator[GithubUser, None, None]:
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

            yield GithubUser(username=username, github_hostname=github_hostname)
