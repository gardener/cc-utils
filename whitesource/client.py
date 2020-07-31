import json

import requests
from requests_toolbelt import MultipartEncoder

from ci.util import urljoin
import model.whitesource


class WSNotOkayException(Exception):
    def __init__(self, res: requests.Response, msg: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.res = res
        self.msg = msg


class WhitesourceClient:

    def __init__(self,
                 whitesource_cfg: model.whitesource.WhitesourceConfig):
        self.routes = WhitesourceRoutes(extension_endpoint=whitesource_cfg.extension_endpoint())
        self.config = whitesource_cfg
        self.creds = self.config.credentials()

    def request(self, method: str, print_error: bool = True, *args, **kwargs):
        res = requests.request(method=method,
                               verify=False,
                               *args, **kwargs)
        if not res.ok:
            msg = f'{method} request to url {res.url} failed with {res.status_code=} {res.reason=}'
            if print_error:
                print(msg)
                print(res.text)
            raise WSNotOkayException(res=res, msg=msg)
        return res

    def post_product(self,
                     product_token: str,
                     component_name: str,
                     requester_email: str,
                     extra_whitesource_config: {},
                     file):

        config = {
            "componentName": component_name,
            "requesterEmail": requester_email,
            "productToken": product_token,
            "userKey": self.creds.user_key(),
            "apiKey": self.config.api_key(),
            "wssEndpoint": self.config.wss_endpoint(),
            "optional": json.dumps(extra_whitesource_config)
        }

        m = MultipartEncoder(
            fields={"config": json.dumps(config),
                    'component': ('component.tar.gz', file, 'text/plain')}
        )
        return self.request(method="POST",
                            url=self.routes.post_component(),
                            headers={'Content-Type': m.content_type},
                            data=m)


class WhitesourceRoutes:

    def __init__(self, extension_endpoint: str):
        self.extension_endpoint = extension_endpoint

    def post_component(self):
        return urljoin(self.extension_endpoint, 'component')
