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

import datetime
import json
import logging
import urllib.parse

import requests
import urllib3
import urllib3.util.retry

import ci.util
import clamav.model
import clamav.routes


logger = logging.getLogger(__name__)


class ClamAVRoutes:
    def __init__(
        self,
        base_url: str,
    ):
        self._base_url = base_url

    def scan(self):
        return ci.util.urljoin(self._base_url, 'scan')

    def version(self):
        return ci.util.urljoin(self._base_url, 'version')


def _make_latin1_encodable(value: str, /) -> str:
    try:
        value.encode('latin-1')
        return value
    except UnicodeEncodeError as ue:
        invalid_char = value[ue.start:ue.end]
        encoded_char = urllib.parse.quote(invalid_char)
        value = value.replace(invalid_char, encoded_char)
        return _make_latin1_encodable(value)


class ClamAVClient:
    def __init__(
        self,
        routes: clamav.routes.ClamAVRoutes,
        retry_cfg: urllib3.util.retry.Retry=None,
        max_parallel:int=8,
    ):
        self.routes = routes
        self.http = urllib3.PoolManager(
            retries=retry_cfg,
            maxsize=max_parallel,
        )

    def _request(self, *args, **kwargs):
        res = self.http.request(
            *args,
            **kwargs,
        )
        if res.status < 200 or res.status > 200:
            raise urllib3.exceptions.HTTPError(f'{res.status=} {res.data=}')

        if 'preload_content' in kwargs and not kwargs['preload_content']:
            body = b''
            for chunk in res.stream():
                body += chunk

            res.drain_conn()
            res.release_conn()
        else:
            body = res.data

        parsed = json.loads(body)
        return parsed

    def scan(
        self,
        data,
        timeout_seconds:float=60*15,
        content_length_octets:int=None,
        name: str=None,
    ) -> clamav.model.ScanResult:
        url = self.routes.scan()

        if content_length_octets:
            headers = {'Content-Length': str(content_length_octets)}
        else:
            headers = {}

        if name:
            headers['Name'] = _make_latin1_encodable(name)

        try:
            response = self._request(
                method='POST',
                url=url,
                body=data,
                headers=headers,
                timeout=timeout_seconds,
                preload_content=False,
            )
        except (
            requests.exceptions.ChunkedEncodingError,
            requests.exceptions.ConnectionError,
            urllib3.exceptions.HTTPError,
        ) as ce:
            if (rq := getattr(ce, 'request', None)):
                rq_url = getattr(rq, 'url', '<unknown>')
                if rq_url != url:
                    url = f'{url=}, {rq_url=}'

            logger.warning(f'{name=}: {ce=} {url=}')
            return clamav.model.ScanResult(
                status=clamav.model.ScanStatus.SCAN_FAILED,
                details=f'{ce=}',
                malware_status=clamav.model.MalwareStatus.UNKNOWN,
                meta=None,
                name=name,
            )

        message = response.get('message', None)
        details = response.get('details', 'no details available')

        if (malware_status_str := response['result']) == 'OK':
            malware_status = clamav.model.MalwareStatus.OK
            details = message or 'no details available'
        elif malware_status_str == 'FOUND_MALWARE':
            malware_status = clamav.model.MalwareStatus.FOUND_MALWARE
            details = f'{message}: {details}'
        elif malware_status_str == 'unknown':
            malware_status = clamav.model.MalwareStatus.UNKNOWN
        else:
            raise NotImplementedError(malware_status_str)

        return clamav.model.ScanResult(
            status=clamav.model.ScanStatus.SCAN_SUCCEEDED,
            details=details,
            malware_status=malware_status,
            meta=clamav.model.Meta(**response.get('meta')),
            name=name,
        )

    def clamav_version_info(
        self,
    ) -> clamav.model.ClamAVVersionInfo:
        parsed_response = self._request(
            method='GET',
            url=self.routes.version(),
        )
        return clamav.model.ClamAVVersionInfo(
            clamav_version_str=parsed_response['clamav_version_str'],
            signature_version=parsed_response['signature_version'],
            signature_date=datetime.datetime.fromisoformat(parsed_response['signature_date']),
        )
