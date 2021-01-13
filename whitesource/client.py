import json
import typing
import websockets

import requests

import ci.util
import whitesource.model


class WSNotOkayException(Exception):
    def __init__(self, res: requests.Response, msg: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.res = res
        self.msg = msg


class WhitesourceClient:

    def __init__(
        self,
        api_key: str,
        extension_endpoint: str,
        product_token: str,
        wss_api_endpoint: str,
        wss_endpoint: str,
        ws_creds,
    ):
        self.routes = WhitesourceRoutes(
            extension_endpoint=extension_endpoint,
            wss_api_endpoint=wss_api_endpoint,
        )
        self.api_key = api_key
        self.wss_endpoint = wss_endpoint
        self.creds = ws_creds
        self.product_token = product_token

    def request(self, method: str, print_error: bool = True, *args, **kwargs):
        res = requests.request(
            method=method,
            *args, **kwargs,
        )
        if not res.ok:
            msg = f'{method} request to url {res.url} failed with {res.status_code=} {res.reason=}'
            if print_error:
                ci.util.error(msg)
                ci.util.error(res.text)
            raise WSNotOkayException(res=res, msg=msg)
        return res

    def save_project_tag(self, projectToken: str, key: str, value: str):
        body = {
            'requestType': 'saveProjectTag',
            'userKey': self.creds.user_key(),
            'projectToken': projectToken,
            'tagKey': key,
            'tagValue': value,
        }

        return self.request(
            method='POST',
            url=self.routes.wss_api_endpoint,
            headers={'content-type': 'application/json'},
            json=body,
        )

    def ws_project(
        self,
        extra_whitesource_config: typing.Dict,
        file,
        project_name: str,
        requester_email: str,
        length: int,
        chunk_size: int,
        ping_interval: int,
        ping_timeout: int,
    ):

        meta_data = {
            'chunkSize': chunk_size,
            'length': length
        }

        ws_config = {
            'apiKey': self.api_key,
            'extraWsConfig': extra_whitesource_config,
            'productToken': self.product_token,
            'projectName': project_name,
            'requesterEmail': requester_email,
            'userKey': self.creds.user_key(),
            'wssUrl': self.wss_endpoint,
        }

        async with websockets.connect(
            uri=self.routes.ws_component(),
            ping_interval=ping_interval,
            ping_timeout=ping_timeout,
            ) as websocket:
            await websocket.send(json.dumps(meta_data))
            await websocket.send(json.dumps(ws_config))
            with open(file, 'rb') as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    await websocket.send(chunk)
            return await websocket.recv()

    def get_product_risk_report(self):
        body = {
            'requestType': 'getProductRiskReport',
            'userKey': self.creds.user_key(),
            'productToken': self.product_token,
        }
        return self.request(
            method='POST',
            url=self.routes.get_product_risk_report(),
            headers={'content-type': 'application/json'},
            json=body,
        )

    def get_all_projects_of_product(
        self,
    ) -> typing.List[whitesource.model.WhitesourceProject]:
        body = {
            'requestType': 'getAllProjects',
            'userKey': self.creds.user_key(),
            'productToken': self.product_token,
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

    def ws_component(self):
        return ci.util.urljoin(self.extension_endpoint, 'component')

    def get_product_risk_report(self):
        return ci.util.urljoin(self.wss_api_endpoint)

    def get_all_projects(self):
        return ci.util.urljoin(self.wss_api_endpoint)

    def get_project_vulnerability_report(self):
        return ci.util.urljoin(self.wss_api_endpoint)
