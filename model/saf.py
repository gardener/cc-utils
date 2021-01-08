import dataclasses

import model.base

'''
cfg for `SAF evidence API`
'''


@dataclasses.dataclass
class SafCredentials:
    bearer_token: str


class SafApiCfg(model.base.NamedModelElement):
    def base_url(self):
        return self.raw['base_url']

    def credentials(self):
        return SafCredentials(**self.raw['credentials'])
