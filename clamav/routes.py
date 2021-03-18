# Copyright (c) 2019-2020 SAP SE or an SAP affiliate company. All rights reserved. This file is
# licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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
