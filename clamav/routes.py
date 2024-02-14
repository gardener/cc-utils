# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


from ensure import ensure_annotations
from ci.util import urljoin


class ClamAVRoutes:
    '''ClamAV REST API endpoint URLs for the ClamAV base URL.

    Not intended to be instantiated by users of this module.
    '''
    @ensure_annotations
    def __init__(self, base_url: str):
        '''
        @param base_url: the ClamAV cluster-service URL
        '''
        self.base_url = base_url

    def _api_url(self, *parts, **kwargs):
        return urljoin(self.base_url, *parts)

    def scan(self):
        return self._api_url('scan')

    def sse_scan(self):
        return self._api_url('sse', 'scan')

    def info(self):
        return self._api_url('info')

    def monitor(self):
        return self._api_url('monitor')

    def signature_version(self):
        return self._api_url('signature-version')

    def health(self):
        return self._api_url('mwss-health')

    def version(self):
        return self._api_url('version')
