import dacite

import model.base


class GcpServiceAccount(model.base.NamedModelElement):
    def service_account_key(self):
        '''
        service-account-key (credentials) as retrieved from GCP's IAM & Admin console
        '''
        return self.raw['service_account_key']

    def service_account_credentials(self) -> 'google.oauth2.service_account.Credentials':
        import google.oauth2.service_account
        return google.oauth2.service_account.Credentials.from_service_account_info(
            self.service_account_key()
        )

    def project(self):
        return self.raw['project']

    def client_email(self) -> str:
        return self.service_account_key()['client_email']

    def private_key_id(self) -> str:
        return self.service_account_key()['private_key_id']

    def rotation_cfg(self) -> model.base.CfgElementReference:
        '''
        used to specify cfg-element to use for cross-rotation
        '''
        raw = self.raw.get('rotation_cfg')
        if raw:
            return dacite.from_dict(
                data_class=model.base.CfgElementReference,
                data=raw,
            )

        return None

    def _required_attributes(self):
        return ['service_account_key','project']
