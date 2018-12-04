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

import elasticsearch

import model.elasticsearch
import util


def from_cfg(
    elasticsearch_cfg:model.elasticsearch.ElasticSearchConfig
):
    return ElasticSearchClient(
        elasticsearch=_from_cfg(elasticsearch_cfg=elasticsearch_cfg)
    )


def _from_cfg(
    elasticsearch_cfg:model.elasticsearch.ElasticSearchConfig
):
    return elasticsearch.Elasticsearch(elasticsearch_cfg.endpoints())


class ElasticSearchClient(object):
    def __init__(
        self,
        elasticsearch: elasticsearch.Elasticsearch,
    ):
        self._api = elasticsearch


    def store_document(
        self,
        index: str,
        body: dict,
        *args,
        **kwargs,
    ):
        util.check_type(index, str)
        util.check_type(body, dict)
        if 'doc_type' in kwargs:
            raise ValueError()

        return self._api.index(
            index=index,
            doc_type='_doc',
            body=body,
            *args,
            **kwargs,
        )
