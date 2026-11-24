from model.base import (
    NamedModelElement,
)


class BtpApplicationCertificate(NamedModelElement):
    def subject_template(self) -> str:
        '''
        certificate subject template to inject CN
        '''
        return self.raw['subject_template']

    def common_name(self) -> str:
        '''
        subject common name
        '''
        return self.raw['common_name']

    def scopes(self) -> list[str]:
        '''
        scopes for certificate
        '''
        return self.raw['scopes']

    def subject(self, cn: str) -> str:
        return self.subject_template().format(cn=cn)

    @staticmethod
    def parse_serial_no_from_common_name(cn: str) -> tuple[int, str]:
        '''
        Parses serial number and base from the common name.
        It expects a common name of the format '<serial_no>.<base>', otherwise
        a ValueError is raised.
        '''
        idx = cn.find('.')
        if idx <= 0:
            return 0, cn
        try:
            return int(cn[:idx]), cn[idx+1:]
        except ValueError:
            raise ValueError(f'unexpected cn: {cn}')

    def cert_service_binding(self):
        '''
        service binding used for authentication on certificate-service
        '''
        return self.raw['cert_service_binding']

    def auth_application_certificate(self):
        '''
        application certificate used for authentication on service SPS
        '''
        return self.raw['auth_application_certificate']

    def application_endpoint(self):
        '''
        endpoint for managing certificates
        '''
        return self.raw['application_endpoint']

    def application_id(self):
        '''
        application id
        '''
        return self.raw['application_id']

    def client_id(self):
        '''
        client id for application API
        '''
        return self.raw['client_id']

    def certificate_pem(self):
        '''
        certificate as PEM
        '''
        return self.raw['certificate_pem']

    def private_key_pem(self):
        '''
        certificate private key as PEM
        '''
        return self.raw['private_key_pem']

    def validity_in_days(self):
        '''
        certificate validity in days
        '''
        return self.raw['validity_in_days']

    def _required_attributes(self):
        return ['subject_template', 'common_name', 'validity_in_days',
                'cert_service_binding', 'auth_application_certificate',
                'application_endpoint', 'application_id', 'client_id',
                'scopes', 'certificate_pem', 'private_key_pem']
