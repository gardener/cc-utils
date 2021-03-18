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
import logging
import requests

from ensure import ensure_annotations
from http_requests import check_http_code
from .routes import ClamAVRoutes
from clamav.util import iter_image_files

from .model import (
    ClamAVHealth,
    ClamAVInfo,
    ClamAVMonitoringInfo,
    ClamAVScanEventClient,
    ClamAVScanResult,
    ClamAVError,
    ERROR_CODE_ON_SCAN_ABORTED,
)

logger = logging.getLogger(__name__)


class ClamAVClient:
    @ensure_annotations
    def __init__(
        self,
        routes: ClamAVRoutes,
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

    def sse_scan(self, data):
        url = self.routes.sse_scan()
        client = ClamAVScanEventClient(
            self._request(self._session.post, url=url, data=data, stream=True)
        )
        return client.process_events()

    def health(self):
        url = self.routes.health()
        response = self._request(self._session.get, url)
        return ClamAVHealth(response.json())

    def scan_container_image(self, image_reference: str):
        '''Fetch and scan the container image with the given image reference using ClamAV
        '''
        logger.debug(f'scanning container image {image_reference}')
        for content, path in iter_image_files(image_reference):
            try:
                scan_result = self.sse_scan(content)
                if not scan_result.malware_detected():
                    continue
                else:
                    yield (scan_result, path)
            except ClamAVError as e:
                if e.error_code() == ERROR_CODE_ON_SCAN_ABORTED:
                    yield (
                        ClamAVScanResult({'finding': f'Scan aborted: {e.error_message()}'}), path
                    )
                else:
                    raise e
