import json
import model.whitesource
import requests
from requests_toolbelt import MultipartEncoder


class WSNotOkayException(Exception):
    def __init__(self, res: requests.Response, msg: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.res = res
        self.msg = msg


class WhitesourceClient:

    def __init__(self,
                 whitesource_cfg: model.whitesource.WhitesourceConfig):
        self.routes = WhitesourceRoutes(base_url=whitesource_cfg.base_url())
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

    def post_component(self,
                       product_token: str,
                       component_name: str,
                       requester_email: str,
                       optional_config: dict,
                       component):
        m = MultipartEncoder(
            fields={'componentName': component_name,
                    'requesterEmail': requester_email,
                    'productToken': product_token,
                    'userKey': self.creds.user_key(),
                    'apiKey': self.config.api_key(),
                    'apiBaseUrl': self.config.base_url(),
                    'optionalConfig': json.dumps(optional_config),
                    'component': (component, open(component, 'rb'), 'text/plain')}
        )
        return self.request(method="POST",
                            url=self.routes.post_component(),
                            headers={'Content-Type': m.content_type},
                            data=m.to_string())


class WhitesourceRoutes:

    def __init__(self, base_url: str):
        self.base_url = base_url

    def post_component(self):
        # TODO use API endpoint
        return "http://127.0.0.1:8000/component"
        # return urljoin(self.base_url, 'component')
