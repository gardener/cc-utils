# Copyright (c) 2019 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
# under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

import requests

from ensure import ensure_annotations
from http_requests import check_http_code
from .routes import ClamAVRoutes

from .model import (
    ClamAVInfo,
    ClamAVMonitoringInfo,
    ClamAVScanResult,
    ClamAVHealth,
)


class ClamAVApi(object):
    '''Implements ClamAV REST API functionality.
    '''
    @ensure_annotations
    def __init__(
        self,
        routes:ClamAVRoutes,
    ):
        self.routes = routes
        self._session = requests.Session()

    @check_http_code
    def _request(self, function, *args, **kwargs):
        return function(*args, **kwargs)

    def info(self):
        url = self.routes.info()
        response = self._request(self._session.get, url)
        return ClamAVInfo(response.json())

    def monitor(self):
        url = self.routes.monitor()
        response = self._request(self._session.get, url)
        return ClamAVMonitoringInfo(response.json())

    def scan(self, data):
        url = self.routes.scan()
        response = self._request(self._session.post, url=url, data=data)
        return ClamAVScanResult(response.json())

    def health(self):
        url = self.routes.health()
        response = self._request(self._session.get, url)
        return ClamAVHealth(response.json())
