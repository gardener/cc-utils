from model.base import (
    NamedModelElement,
)


class BtpServiceBinding(NamedModelElement):
    def credentials(self):
        '''
        service manager credentials (JSON) as retrieved from SAP BTP cockpit
        '''
        return self.raw['credentials']

    def instance_id(self):
        '''
        instance id of the service to create a service binding
        '''
        return self.raw['instance_id']

    def prefix(self):
        s = self.name()
        if s[-1] != '-':
            s += '-'
        return s

    def binding_id(self):
        '''
        binding id
        '''
        return self.raw['binding_id']

    def binding_name(self):
        '''
        binding name
        '''
        return self.raw['binding_name']

    def auth_service_binding(self):
        '''
        service binding used for authentication on service-manager
        '''
        return self.raw['auth_service_binding']

    def _required_attributes(self):
        return ['credentials', 'instance_id', 'binding_id', 'binding_name', 'auth_service_binding']
