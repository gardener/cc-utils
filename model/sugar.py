from model.base import (
    NamedModelElement,
)


class Sugar(NamedModelElement):
    '''
    Solinas Universal GitHub Actions Runner (SUGAR) for github.tools.sap
    see https://wiki.one.int.sap/wiki/display/DevFw/SUGAR#SUGAR-RegisterfortheSUGARservice
    '''
    def github(self):
        '''
        github config name
        '''
        return self.raw['github']

    def credentials(self):
        '''
        credentials (service_account and password) used for updating github_token
        '''
        return self.raw['credentials']

    def _required_attributes(self):
        return ['github', 'credentials']
