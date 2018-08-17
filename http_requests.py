# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
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
from functools import wraps

import requests
from requests.auth import HTTPBasicAuth
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from util import warning

default_http_adapter = HTTPAdapter(
    max_retries = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        redirect=False,
        status_forcelist=[500, 502, 503],
        raise_on_status=False,
        respect_retry_after_header=True,
    )
)


def check_http_code(function):
    '''
    a decorator that will check on `requests.Response` instances returned by HTTP requests
    issued with `requests`. In case the response code indicates an error, a warning is logged
    and a `requests.HTTPError` is raised.

    @param: the function to wrap; should be `requests.<http-verb>`, e.g. requests.get
    @raises: `requests.HTTPError` if response's status code indicates an error
    '''
    @wraps(function)
    def http_checker(*args, **kwargs):
        result = function(*args, **kwargs)
        if result.status_code < 200 or result.status_code >= 300:
            url = kwargs.get('url', None)
            warning('{c} - {m}: {u}'.format(c=result.status_code, m=result.content, u=url))
        result.raise_for_status()
        return result
    return http_checker


class AuthenticatedRequestBuilder(object):
    '''
    Wrapper around the 'requests' library, handling concourse-specific
    http headers and also checking for http response codes.

    Not intended to be used outside of this module.
    '''

    def __init__(
            self,
            auth_token: str=None,
            basic_auth_username: str=None,
            basic_auth_passwd: str=None,
            verify_ssl: bool=True
    ):
        self.headers = None
        self.auth = None

        if auth_token:
            self.headers={'Authorization': 'Bearer {}'.format(auth_token)}
        if basic_auth_username and basic_auth_passwd:
            self.auth = HTTPBasicAuth(basic_auth_username, basic_auth_passwd)

        # create session and mount our default adapter (for retry-semantics)
        session = requests.Session()
        session.mount('http://', default_http_adapter)
        session.mount('https://', default_http_adapter)
        self.session = session

        self.verify_ssl = verify_ssl

    def _check_http_code(self, result, url):
        if result.status_code < 200 or result.status_code >= 300:
            warning('{c} - {m}: {u}'.format(c=result.status_code, m=result.content, u=url))
            raise RuntimeError()

    def _request(self,
            method, url: str,
            return_type: str='json',
            check_http_code=True,
            **kwargs
        ):
        headers = self.headers.copy() if self.headers else {}
        if 'headers' in kwargs:
            headers.update(kwargs['headers'])
            del kwargs['headers']
        if 'data' in kwargs:
            headers['content-type'] = 'application/x-yaml'

        result = method(
            url,
            headers=headers,
            auth=self.auth,
            verify=self.verify_ssl,
            **kwargs
        )

        if check_http_code:
            self._check_http_code(result, url)

        if return_type == 'json':
            return result.json()

        return result

    def get(self, url: str, return_type: str='json', **kwargs):
        return self._request(
                method=self.session.get,
                url=url,
                return_type=return_type,
                **kwargs
        )

    def put(self, url: str, body, **kwargs):
        return self._request(
                method=self.session.put,
                url=url,
                return_type=None,
                data=str(body),
                **kwargs
        )

    def post(self, url: str, body, **kwargs):
        return self._request(
                method=self.session.post,
                url=url,
                return_type=None,
                data=str(body),
                **kwargs
        )

    def delete(self, url: str, return_type=None, **kwargs):
        return self._request(
                method=self.session.delete,
                url=url,
                return_type=None,
                **kwargs
        )
