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

from functools import partial
from urllib.parse import urlencode

import requests

from util import not_empty, not_none, urljoin
from http_requests import AuthenticatedRequestBuilder

class ProtecodeApiRoutes(object):
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
        return self._api_url('upload')

    def product(self, product_id: int):
        return self._api_url('product')

    def product_custom_data(self, product_id: int):
        return self._api_url('product', str(product_id), 'custom-data')


class ProtecodeApi(object):
    def __init__(self, api_routes, basic_credentials, tls_verify=False):
        self._routes = not_none(api_routes)
        self._credentials = not_none(basic_credentials)
        self._auth = (basic_credentials.username(), basic_credentials.passwd())
        self._request_builder = AuthenticatedRequestBuilder(
            basic_auth_username=basic_credentials.username(),
            basic_auth_passwd=basic_credentials.passwd(),
            verify_ssl=tls_verify
        )

        self._get = partial(requests.get, verify=tls_verify)
        self._post = partial(requests.post, verify=tls_verify)
        self._put = partial(requests.put, verify=tls_verify)

    def upload(self, application_name, group_id, data, custom_attribs={}):
        url = self._routes.upload(file_name=application_name)
        headers = {'Group': str(group_id)}
        headers.update({'META-' + k: v for k,v in custom_attribs.items()})

        result = self._put(
            url=url,
            headers=headers,
            auth=self._auth,
            data=data,
        )

        return result.json()

    def list_apps(self, group_id, custom_attribs={}):
        url = self._routes.apps(group_id=group_id, custom_attribs=custom_attribs)

        result = self._get(
            url=url,
            auth=self._auth,
        )
        return result.json()

    def set_metadata(self, product_id: int, custom_attribs: dict):
        url = self._routes.product_custom_data(product_id=product_id)
        headers = ('META-' + key + ':' + value for key, value in custom_attribs)

        result = self._post(
            url=url,
            auth=self._auth,
            headers=headers
        )
        return result.json()

    def metadata(self, product_id: int):
        url = self._routes.product_custom_data(product_id=product_id)

        result = self._post(
            url=url,
            auth=self._auth,
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
