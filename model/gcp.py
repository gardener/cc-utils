from model.base import (
    NamedModelElement,
)


class GcpServiceAccount(NamedModelElement):
    def service_account_key(self):
        '''
        service-account-key (credentials) as retrieved from GCP's IAM & Admin console
        '''
        return self.raw['service_account_key']

    def service_account_credentials(self): # -> 'google.oauth2.service_account.Credentials':
        import google.oauth2.service_account
        return google.oauth2.service_account.Credentials.from_service_account_info(
            self.service_account_key()
        )

    def project(self):
        return self.raw['project']

    def _required_attributes(self):
        return ['service_account_key','project']
