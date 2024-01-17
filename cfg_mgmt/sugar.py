import copy
import logging
import typing
import requests

import cfg_mgmt
import ci.log
import ci.util
import model
import model.sugar
import model.github


ci.log.configure_default_logging()
logger = logging.getLogger(__name__)


class SugarUpdateClient:
    '''
    client to update github token for solinas
    '''
    def __init__(
        self,
        service_account: str,
        password: str,
        github_token: str,
    ):
        self.service_account = service_account
        self.password = password
        self.github_token = github_token

    def update_github_token(self, team_id: str):
        '''
        updates github token for solinas
        see https://wiki.one.int.sap/wiki/display/DevFw/SUGAR#SUGAR-UpdatingyourGitHubaccess-token
        '''
        url = 'https://api.solinas.sap.corp/api/v1/sugar/customer'
        data = {
            'id': team_id,
            'githubToken': self.github_token,
        }
        resp = requests.put(
            url,
            json=data,
            timeout=(4, 31),
            auth=(self.service_account, self.password),
        )
        if not resp.ok:
            msg = f'update_github_token failed: {resp.status_code} {resp.text}'
            logger.error(msg)
            raise requests.HTTPError(msg)
        logger.info('Updated github_token for %s', id)


def _authenticate(
    cfg_element: model.sugar.Sugar,
    cfg_factory: model.ConfigFactory,
) -> SugarUpdateClient:
    team_id = cfg_element.name()
    credentials = cfg_factory.sugar(team_id).credentials()
    service_account = credentials['service_account']
    password = credentials['password']
    github = cfg_element.github()
    auth_token = cfg_factory.github(github).credentials().auth_token()
    return SugarUpdateClient(service_account, password, auth_token)


def rotate_cfg_element(
    cfg_element: model.sugar.Sugar,
    cfg_factory: model.ConfigFactory,
) -> typing.Tuple[cfg_mgmt.revert_function, dict, model.NamedModelElement]:
    client = _authenticate(cfg_element, cfg_factory)
    team_id = cfg_element.name()
    client.update_github_token(team_id)

    secret_id = {'team_id': team_id}
    raw_cfg = copy.deepcopy(cfg_element.raw)
    updated_elem = model.sugar.Sugar(
        name=cfg_element.name(), raw_dict=raw_cfg, type_name=cfg_element._type_name
    )

    def revert():
        pass

    return revert, secret_id, updated_elem
