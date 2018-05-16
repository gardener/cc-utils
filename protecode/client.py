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

from util import not_empty, not_none, urljoin

class ProtecodeApiRoutes(object):
    def __init__(self, base_url):
        self._base_url = not_empty(base_url)
        self._api_url = partial(self._url, 'api')

    def _url(self, *parts):
        return urljoin(self._base_url, *parts)

    def groups(self):
        return self._api_url('groups')

    def upload(self, file_name):
        return self._api_url('upload')

    def product(self, product_id: int):
        return self._api_url('product')


class ProtecodeApi(object):
    def __init__(self, api_routes, basic_credentials):
        self._routes = not_none(api_routes)
        self._credentials = not_none(basic_credentials)
