from ci.util import urljoin
import model.checkmarx
import requests
import dataclasses
import datetime


def require_auth(f: callable):
    def wrapper(checkmarx_client: 'CheckmarxClient'):
        checkmarx_client._auth()

    return wrapper


class CheckmarxRoutes:
    '''Checkmarx REST API endpoints for the checkmarx base URL.
    '''
    def __init__(self, base_url: str):
        self.base_url = base_url

    def _api_url(self, *parts, **kwargs):
        return urljoin(self.base_url, 'cxrestapi', *parts)

    def auth(self):
        return self._api_url('auth','identity','connect','token')

    def projects(self):
        return self._api_url('projects')


@dataclasses.dataclass
class AuthResponse:
    access_token: str
    expires_in: int
    token_type: str
    expires_at: datetime.datetime = None

    def is_valid(self):
        return datetime.datetime.now() > self.expires_at


class CheckmarxClient:
    def __init__(self, checkmarx_cfg: model.checkmarx.CheckmarxConfig):
        self.routes = CheckmarxRoutes(base_url=checkmarx_cfg.base_url())
        self.config = checkmarx_cfg
        self.auth = None

    def _auth(self):
        if self.auth and self.auth.is_valid():
            return self.auth

        creds = self.config.credentials()
        res = requests.post(
            self.routes.auth(),
            data={
                'username': creds.qualified_username(),
                'password': creds.passwd(),
                'client_id': creds.client_id(),
                'client_secret': creds.client_secret(),
                'scope': creds.scope(),
                'grant_type': 'password',
            },
            verify=False,
        )
        res = AuthResponse(**res.json())
        res.expires_at = datetime.datetime.fromtimestamp(
                datetime.datetime.now().timestamp() + res.expires_in - 10
        )

        self.auth = res
        return res

    @require_auth
    def projects(self):
        pass
