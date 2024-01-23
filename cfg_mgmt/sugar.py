import logging
import requests

import ci.log
import ci.util


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
        logger.info(f'Updated github_token for {team_id=}')
