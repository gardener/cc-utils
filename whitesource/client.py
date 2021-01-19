import json
import typing
import websockets

import requests

import ci.util
import dso.util
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
        requester_mail: str,
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
        self.requester_mail = requester_mail

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

    async def upload_to_project(
        self,
        extra_whitesource_config: typing.Dict,
        file: typing.IO,
        project_name: str,
        length: int,
        chunk_size=1024,
        ping_interval=1000,
        ping_timeout=1000,
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
            'requesterEmail': self.requester_mail,
            'userKey': self.creds.user_key(),
            'wssUrl': self.wss_endpoint,
        }

        async with websockets.connect(
            uri=self.routes.upload_to_project(),
            ping_interval=ping_interval,
            ping_timeout=ping_timeout,
        ) as websocket:
            await websocket.send(json.dumps(meta_data))
            await websocket.send(json.dumps(ws_config))
            sent = 0
            while sent < length:
                chunk = file.read(chunk_size)
                if len(chunk) == 0:
                    await websocket.close()
                    raise OSError('Desired length does not fit actual file length')
                await websocket.send(chunk)
                sent += len(chunk)

            return json.loads(await websocket.recv())

    def get_product_risk_report(self):
        clogger = dso.util.component_logger(__name__)
        clogger.info('retrieving product risk report')
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
    ) -> typing.List[whitesource.model.WhiteSrcProject]:
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

        projects: typing.List[whitesource.model.WhiteSrcProject] = []
        for element in res['projects']:
            projects.append(whitesource.model.WhiteSrcProject(
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

    def upload_to_project(self):
        return ci.util.urljoin(self.extension_endpoint, 'component')

    def get_product_risk_report(self):
        return ci.util.urljoin(self.wss_api_endpoint)

    def get_all_projects(self):
        return ci.util.urljoin(self.wss_api_endpoint)

    def get_project_vulnerability_report(self):
        return ci.util.urljoin(self.wss_api_endpoint)
