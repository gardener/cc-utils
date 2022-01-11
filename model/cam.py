from model.base import (
    BasicCredentials,
    NamedModelElement,
)


class CAMCfg(NamedModelElement):
    def credentials(self):
        return BasicCredentials(self.raw.get('credentials'))

    def api_base_url(self):
        return self.raw.get('api_base_url')

    def _required_attributes(self):
        return (
            'credentials',
            'api_base_url',
        )
