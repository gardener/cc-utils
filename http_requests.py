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
from functools import wraps

import traceback
import datetime
import requests
from requests.auth import HTTPBasicAuth
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import ccc.elasticsearch
import util
from util import warning, info, ctx


class LoggingRetry(Retry):
    def increment(self,
        method=None,
        url=None,
        response=None,
        error=None,
        _pool=None,
        _stacktrace=None
    ):
        # super().increment will either raise an exception indicating that no retry is to
        # be performed or return a new, modified instance of this class
        retry = super().increment(method, url, response, error, _pool, _stacktrace)
        # Use the Retry history to determine the number of retries.
        num_retries = len(self.history) if self.history else 0
        # Retrieve host from underlying connection pool and
        host = _pool.host
        warning(
            f'HTTP request (host: {host}, url: {url}, method: {method}) unsuccessful. '
            f'Retries so far: {num_retries}. Retrying ...'
        )
        return retry


def mount_default_adapter(
    session: requests.Session,
    connection_pool_cache_size=10, # requests-library default
    max_pool_size=10, # requests-library default
):
    default_http_adapter = HTTPAdapter(
        pool_connections = connection_pool_cache_size,
        pool_maxsize = max_pool_size,
        max_retries = LoggingRetry(
            total=3,
            connect=3,
            read=3,
            status=3,
            redirect=False,
            status_forcelist=[500, 502, 503],
            raise_on_status=False,
            respect_retry_after_header=True,
            backoff_factor=1.0,
        )
    )
    session.mount('http://', default_http_adapter)
    session.mount('https://', default_http_adapter)

    return session


def log_stack_trace_information(resp, *args, **kwargs):
    '''
    This function stores the current stacktrace in elastic search.
    It must not return anything, otherwise the return value is assumed to replace the response
    '''
    if not util._running_on_ci():
        return # early exit if not running in ci job

    config_set_name = util.check_env('CONCOURSE_CURRENT_CFG')
    try:
        els_index = 'github_access_stacktrace'
        try:
            config_set = ctx().cfg_factory().cfg_set(config_set_name)
        except KeyError:
            # do nothing: external concourse does not have config set 'internal_active'
            return
        elastic_cfg = config_set.elasticsearch()

        now = datetime.datetime.utcnow()
        json_body = {
            'date': now.isoformat(),
            'url': resp.url,
            'req_method': resp.request.method,
            'stacktrace': traceback.format_stack()
        }

        elastic_client = ccc.elasticsearch.from_cfg(elasticsearch_cfg=elastic_cfg)
        elastic_client.store_document(
            index=els_index,
            body=json_body
        )

    except Exception as e:
        info(f'Could not log stack trace information: {e}')


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
        self.session = mount_default_adapter(requests.Session())

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
            if 'content-type' not in headers:
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
