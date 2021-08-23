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
import typing

from ensure import ensure_annotations
from http_requests import check_http_code
from .routes import ClamAVRoutes
from clamav.util import iter_image_files

import oci.client as oc
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

    def scan_container_image_layers(
        self,
        image_reference: str,
        oci_client: oc.Client,
    ) -> typing.Generator[tuple[ClamAVScanResult, str], None, None]:
        '''
        uploads the layers of the given OCI-Image-Reference to the underlying malware
        scanning service and returns scan results.
        '''
        manifest = oci_client.manifest(image_reference=image_reference)
        for layer in manifest.layers:
            layer_blob = oci_client.blob(
                image_reference=image_reference,
                digest=layer.digest,
                stream=True,
            )

            try:
                scan_result = self.sse_scan(
                    data=layer_blob.iter_content(chunk_size=4096)
                )
                if not scan_result.malware_detected():
                    logger.info(f'{image_reference=}:{layer_blob.digest=}: no malware found')
                    continue
                else:
                    yield (scan_result, layer.digest)
            except ClamAVError as e:
                if e.error_code() == ERROR_CODE_ON_SCAN_ABORTED:
                    yield (
                        ClamAVScanResult({'finding': f'Scan aborted: {e.error_message()}'}), path
                    )
                else:
                    raise e

    def scan_container_image(self, image_reference: str):
        '''
        XXX: currently broken for (some/all)  gzip-compressed tar-layers
         -> use scan_container_image_layers instead

        Fetch and scan the container image with the given image reference using ClamAV
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
