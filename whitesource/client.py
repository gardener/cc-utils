import dataclasses
import typing

import requests
from requests_toolbelt import MultipartEncoder

from ci.util import urljoin
import model.whitesource
import whitesource.model


@dataclasses.dataclass
class WhitesourceProjectRating:
    project_name: str
    cve_score: float


class WSNotOkayException(Exception):
    def __init__(self, res: requests.Response, msg: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.res = res
        self.msg = msg


class WhitesourceClient:

    def __init__(
        self,
        whitesource_cfg: model.whitesource.WhitesourceConfig,
    ):
        self.routes = WhitesourceRoutes(
            extension_endpoint=whitesource_cfg.extension_endpoint(),
            wss_api_endpoint=whitesource_cfg.wss_api_endpoint(),
        )
        self.config = whitesource_cfg
        self.creds = self.config.credentials()

    def request(self, method: str, print_error: bool = True, *args, **kwargs):
        res = requests.request(
            method=method,
            *args, **kwargs,
        )
        if not res.ok:
            msg = f'{method} request to url {res.url} failed with {res.status_code=} {res.reason=}'
            if print_error:
                print(msg)
                print(res.text)
            raise WSNotOkayException(res=res, msg=msg)
        return res

    def post_product(
        self,
        product_token: str,
        component_name: str,
        requester_email: str,
        component_version: str,
        extra_whitesource_config: typing.Dict,
        file,
    ):

        fields = {
            'projectName': component_name,
            'requesterEmail': requester_email,
            'productToken': product_token,
            'userKey': self.creds.user_key(),
            'apiKey': self.config.api_key(),
            'wss.url': self.config.wss_endpoint(),
            'projectVersion': component_version,
            'includes': '*',
            'component': ('component.tar.gz', file, 'text/plain'),
        }

        # add extra whitesource config
        for key, value in extra_whitesource_config.items():
            fields[key] = value

        m = MultipartEncoder(
            fields=fields,
        )
        return self.request(
            method='POST',
            url=self.routes.post_component(),
            headers={'Content-Type': m.content_type},
            data=m,
        )

    def get_product_risk_report(
        self,
        product_token: str,
    ):
        body = {
            'requestType': 'getProductRiskReport',
            'userKey': self.creds.user_key(),
            'productToken': product_token,
        }
        return self.request(
            method='POST',
            url=self.routes.get_product_risk_report(),
            headers={'content-type': 'application/json'},
            json=body,
        )

    def get_all_projects(
        self,
        product_token: str,
    ):
        body = {
            'requestType': 'getAllProjects',
            'userKey': self.creds.user_key(),
            'productToken': product_token,
        }
        res = self.request(
            method='POST',
            url=self.routes.get_all_projects(),
            json=body,
        )

        res.raise_for_status()
        res = res.json()
        if errorCode := res.get('errorCode'):
            raise requests.HTTPError(f'Error {errorCode}: {res.get("errorMessage")}')

        projects: typing.List[whitesource.model.WhitesourceProject] = []
        for element in res['projects']:
            projects.append(whitesource.model.WhitesourceProject(
                name=element['projectName'],
                token=element['projectName'],
                vulnerability_report=self.get_project_vulnerability_report(
                    project_token=element['projectToken'],
                ),
            ))
        return projects

    def get_project_vulnerability_report(
        self,
        project_token: str,
    ):
        body = {
            'requestType': 'getProjectVulnerabilityReport',
            'userKey': self.creds.user_key(),
            'projectToken': project_token,
            'format': 'json',
        }
        return self.request(
            method='POST',
            url=self.routes.get_project_vulnerability_report(),
            headers={'content-type': 'application/json'},
            json=body,
        ).json()


class WhitesourceRoutes:

    def __init__(
        self,
        extension_endpoint: str,
        wss_api_endpoint: str,
    ):
        self.extension_endpoint = extension_endpoint
        self.wss_api_endpoint = wss_api_endpoint

    def post_component(self):
        return urljoin(self.extension_endpoint, 'component')

    def get_product_risk_report(self):
        return urljoin(self.wss_api_endpoint)

    def get_all_projects(self):
        return urljoin(self.wss_api_endpoint)

    def get_project_vulnerability_report(self):
        return urljoin(self.wss_api_endpoint)
