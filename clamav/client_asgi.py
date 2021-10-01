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

import dataclasses
import enum
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
class Meta:
    scanned_octets: int
    receive_duration_seconds: float
    scan_duration_seconds: float


@dataclasses.dataclass
class ScanResult:
    status: ScanStatus
    details: str
    malware_status: MalwareStatus
    meta: typing.Optional[Meta]
    name: str


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

    def scan(
        self,
        data,
        timeout_seconds:float=60*15,
        content_length_octets:int=None,
        name: str=None,
    ) -> ScanResult:
        url = self.routes.scan()

        if content_length_octets:
            headers = {'Content-Length': str(content_length_octets)}
        else:
            headers = {}

        response = self._request(
            method='POST',
            url=url,
            data=data,
            headers=headers,
            timeout=timeout_seconds,
            stream=True,
        )

        if not response.ok:
            return ScanResult(
                status=ScanStatus.SCAN_FAILED,
                details=f'{response.status_code=} {response.reason=} {response.content=}',
                malware_status=MalwareStatus.UNKNOWN,
                meta=None,
                name=name,
            )

        resp = response.json()

        return ScanResult(
            status=ScanStatus.SCAN_SUCCEEDED,
            details=resp.get('message', 'no details available'),
            malware_status=MalwareStatus(resp['result']),
            meta=Meta(**resp.get('meta')),
            name=name,
        )
