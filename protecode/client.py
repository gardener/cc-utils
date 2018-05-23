# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

from enum import Enum
from functools import partial
from urllib.parse import urlencode, quote
import time

import requests

from util import not_empty, not_none, urljoin
from http_requests import AuthenticatedRequestBuilder, check_http_code
from .model import AnalysisResult, ProcessingStatus


class ProtecodeApiRoutes(object):
    '''
    calculates API routes (URLs) for a subset of the URL endpoints exposed by
    "Protecode" (https://protecode.mo.sap.corp)

    Not intended to be instantiated by users of this module
    '''
    def __init__(self, base_url):
        self._base_url = not_empty(base_url)
        self._api_url = partial(self._url, 'api')

    def _url(self, *parts):
        return urljoin(self._base_url, *parts)

    def apps(self, group_id, custom_attribs={}):
        url = self._api_url('apps')
        if group_id:
            url = urljoin(url, str(group_id))

        search_query = ' '.join(['meta:' + str(k) + '=' + str(v) for k,v in custom_attribs.items()])
        if search_query:
            url += '?' + urlencode({'q': search_query})

        return url

    def groups(self):
        return self._api_url('groups')

    def upload(self, file_name):
        return self._api_url('upload', quote(file_name))

    def product(self, product_id: int):
        return self._api_url('product', str(product_id))

    def product_custom_data(self, product_id: int):
        return self._api_url('product', str(product_id), 'custom-data')


class ProtecodeApi(object):
    def __init__(self, api_routes, basic_credentials, tls_verify=False):
        self._routes = not_none(api_routes)
        self._credentials = not_none(basic_credentials)
        self._auth = (basic_credentials.username(), basic_credentials.passwd())
        self._tls_verify = tls_verify
        self._request_builder = AuthenticatedRequestBuilder(
            basic_auth_username=basic_credentials.username(),
            basic_auth_passwd=basic_credentials.passwd(),
            verify_ssl=tls_verify
        )

    @check_http_code
    def _get(self, *args, **kwargs):
        return partial(requests.get, verify=self._tls_verify, auth=self._auth)(*args, **kwargs)

    @check_http_code
    def _post(self, *args, **kwargs):
        return partial(requests.post, verify=self._tls_verify, auth=self._auth)(*args, **kwargs)

    @check_http_code
    def _put(self, *args, **kwargs):
        return partial(requests.put, verify=self._tls_verify, auth=self._auth)(*args, **kwargs)

    def upload(self, application_name, group_id, data, custom_attribs={}):
        url = self._routes.upload(file_name=application_name)
        headers = {'Group': str(group_id)}
        headers.update({'META-' + k: v for k,v in custom_attribs.items()})

        result = self._put(
            url=url,
            headers=headers,
            data=data,
        )

        return AnalysisResult(raw_dict=result.json().get('results'))

    def scan_result(self, product_id: int):
        url = self._routes.product(product_id=product_id)

        result = self._get(
            url=url,
        ).json()['results']

        return AnalysisResult(raw_dict=result)

    def wait_for_scan_result(self, product_id: int, polling_interval_seconds=10):
        result = self.scan_result(product_id=product_id)
        if result.status() in (ProcessingStatus.READY, ProcessingStatus.FAILED):
            return result
        # keep polling until result is ready
        time.sleep(polling_interval_seconds)
        return self.wait_for_scan_result(
            product_id=product_id,
            polling_interval_seconds=polling_interval_seconds
        )

    def list_apps(self, group_id, custom_attribs={}):
        url = self._routes.apps(group_id=group_id, custom_attribs=custom_attribs)

        result = self._get(
            url=url,
        )
        return [AnalysisResult(p) for p in result.json().get('products')]

    def set_metadata(self, product_id: int, custom_attribs: dict):
        url = self._routes.product_custom_data(product_id=product_id)
        headers = {'META-' + str(key): str(value) for key, value in custom_attribs.items()}

        result = self._post(
            url=url,
            headers=headers,
        )
        return result.json()

    def metadata(self, product_id: int):
        url = self._routes.product_custom_data(product_id=product_id)

        result = self._post(
            url=url,
            headers={},
        )
        return result.json().get('custom_data', {})


def from_cfg(protecode_cfg):
    not_none(protecode_cfg)
    routes = ProtecodeApiRoutes(base_url=protecode_cfg.api_url())
    api = ProtecodeApi(
        api_routes=routes,
        basic_credentials=protecode_cfg.credentials(),
        tls_verify=protecode_cfg.tls_verify()
    )
    return api
