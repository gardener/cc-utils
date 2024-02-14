# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import enum
import functools
import logging

import cachecontrol
import requests

from requests.adapters import HTTPAdapter
from requests.auth import HTTPBasicAuth
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class AdapterFlag(enum.Flag):
    RETRY = enum.auto()
    CACHE = enum.auto()


class LoggingRetry(Retry):
    def __init__(
        self,
        **kwargs,
    ):
        defaults = dict(
            total=3,
            connect=3,
            read=3,
            status=3,
            redirect=False,
            status_forcelist=(429, 500, 502, 503, 504),
            raise_on_status=False,
            respect_retry_after_header=True,
            backoff_factor=1.0,
        )

        super().__init__(**(defaults | kwargs))

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
        logger.warning(
            f'{method=} {url=} returned {response=} {error=} {num_retries=} - trying again'
        )
        return retry


_default_retry_cfg = LoggingRetry()


def mount_default_adapter(
    session: requests.Session,
    connection_pool_cache_size=32, # requests-library default
    max_pool_size=32, # requests-library default
    flags=AdapterFlag.CACHE|AdapterFlag.RETRY,
    retry_cfg: Retry=_default_retry_cfg,
):
    if AdapterFlag.CACHE in flags:
        adapter_constructor = cachecontrol.CacheControlAdapter
    else:
        adapter_constructor = HTTPAdapter

    if AdapterFlag.RETRY in flags:
        adapter_constructor = functools.partial(
            adapter_constructor,
            max_retries=retry_cfg,
        )

    default_http_adapter = adapter_constructor(
        pool_connections=connection_pool_cache_size,
        pool_maxsize=max_pool_size,

    )
    session.mount('http://', default_http_adapter)
    session.mount('https://', default_http_adapter)

    return session


def check_http_code(function):
    '''
    a decorator that will check on `requests.Response` instances returned by HTTP requests
    issued with `requests`. In case the response code indicates an error, a warning is logged
    and a `requests.HTTPError` is raised.

    @param: the function to wrap; should be `requests.<http-verb>`, e.g. requests.get
    @raises: `requests.HTTPError` if response's status code indicates an error
    '''
    @functools.wraps(function)
    def http_checker(*args, **kwargs):
        result = function(*args, **kwargs)
        if not result.ok:
            url = kwargs.get('url', None)
            logger.warning(f'{result.status_code=} - {result.content=}: {url=}')
        result.raise_for_status()
        return result
    return http_checker


class AuthenticatedRequestBuilder:
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
            self.headers = {'Authorization': 'Bearer {}'.format(auth_token)}
        if basic_auth_username and basic_auth_passwd:
            self.auth = HTTPBasicAuth(basic_auth_username, basic_auth_passwd)

        # create session and mount our default adapter (for retry-semantics).
        retry_cfg = LoggingRetry(
            allowed_methods=(*Retry.DEFAULT_ALLOWED_METHODS, 'POST'),
        )

        self.session = mount_default_adapter(
            requests.Session(),
            retry_cfg=retry_cfg,
        )

        self.verify_ssl = verify_ssl

    def _check_http_code(self, result, url):
        if not result.ok:
            logger.warning(
                f'rq against {url=} returned {result.status_code=} {result.content=}'
            )
            result.raise_for_status()

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
        try:
            timeout = kwargs.pop('timeout')
        except KeyError:
            timeout = (4, 31)

        result = method(
            url,
            headers=headers,
            auth=self.auth,
            verify=self.verify_ssl,
            timeout=timeout,
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
