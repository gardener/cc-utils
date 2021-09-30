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
import concurrent.futures
import dataclasses
import enum
import io
import logging
import typing

import requests

import ci.util

logger = logging.getLogger(__name__)


class ScanStatus:
    SCAN_SUCCEEDED = 'scan_succeeded'
    SCAN_FAILED = 'scan_failed'


class MalwareStatus(enum.Enum):
    UNKNOWN = 'unknown'
    FOUND_MALWARE = 'FOUND_MALWARE'
    OK = 'OK'


@dataclasses.dataclass
class ClamAVScanResult:
    status: ScanStatus
    details: str
    malware_status: MalwareStatus


class ClamAVRoutesAsgi:
    def __init__(
        self,
        base_url: str,
    ):
        self._base_url = base_url

    def scan(self):
        return ci.util.urljoin(self._base_url, 'scan')


class ClamAVClientAsgi:
    def __init__(
        self,
        routes: ClamAVRoutesAsgi,
    ):
        self.routes = routes
        self._session = requests.Session()

    def _request(self, *args, **kwargs):
        res =  self._session.request(*args, **kwargs)
        res.raise_for_status()
        return res

    def scan(self, data, timeout_seconds:float=60*15):
        url = self.routes.scan()

        response = self._request(
            method='POST',
            url=url,
            data=data,
            timeout=timeout_seconds,
            stream=True,
        )

        if not response.ok:
            return ClamAVScanResult(
                status=ScanStatus.SCAN_FAILED,
                details=f'{response.status_code=} {response.reason=} {response.content=}',
                malware_status=MalwareStatus.UNKNOWN,
            )

        resp = response.json()

        return ClamAVScanResult(
            status=ScanStatus.SCAN_SUCCEEDED,
            details=resp.get('message', 'no details available'),
            malware_status=MalwareStatus(resp['result']),
        )

    def scan_container_image(
        self,
        content_iterator: typing.Generator[typing.Tuple[typing.IO, str], None, None],
        max_parallel_workers: int=0,
    ):
        logger.info(f'scanning with {max_parallel_workers=}')

        def _scan_content(content_path: typing.Tuple[io.BytesIO, str]):
            content, path = content_path # hack to make compatible w/ ThreadPoolExecutor.map
            scan_result = self.scan(content)
            return (scan_result, path)

        if max_parallel_workers < 2:
            for content, path in content_iterator:
                scan_result, path =  _scan_content(content_path=(content, path))
                yield (scan_result, path)
            return

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_parallel_workers)

        for scan_result, path in executor.map(_scan_content, content_iterator):
            yield (scan_result, path)
