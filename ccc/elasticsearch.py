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
import datetime
import functools
import json
import logging
import os

import elasticsearch

import ci.util
import concourse.util
import model
import model.elasticsearch


logger = logging.getLogger(__name__)


def default_client_if_available(cfg_factory=None) -> 'ElasticSearchClient | None':
    '''
    Returns elasticsearch client if a default config is found.
    '''
    if not ci.util._running_on_ci():
        return None

    if not cfg_factory:
        cfg_factory = ci.util.ctx().cfg_factory()

    if isinstance(cfg_factory, model.ConfigurationSet):
        return from_cfg(cfg_factory.elasticsearch())

    cfg_set = cfg_factory.cfg_set(ci.util.current_config_set_name())
    es_config = cfg_set.elasticsearch()

    return from_cfg(elasticsearch_cfg=es_config)


def from_cfg(
    elasticsearch_cfg:model.elasticsearch.ElasticSearchConfig
):
    return ElasticSearchClient(
        elasticsearch=_from_cfg(elasticsearch_cfg=elasticsearch_cfg)
    )


def _from_cfg(
    elasticsearch_cfg:model.elasticsearch.ElasticSearchConfig
):
    credentials = elasticsearch_cfg.credentials()
    return elasticsearch.Elasticsearch(
        elasticsearch_cfg.endpoints(),
        http_auth=(credentials.username(), credentials.passwd()),
    )


@functools.lru_cache()
def _metadata_dict():
    # XXX mv to concourse package; deduplicate with notify step
    if not ci.util._running_on_ci():
        return {}

    build = concourse.util.find_own_running_build()
    pipeline_metadata = concourse.util.get_pipeline_metadata()
    config_set = ci.util.ctx().cfg_factory().cfg_set(pipeline_metadata.current_config_set_name)
    concourse_cfg = config_set.concourse()

    meta_dict = {
      'build-id': build.id(),
      'build-name': build.build_number(),
      'build-job-name': pipeline_metadata.job_name,
      'build-team-name': pipeline_metadata.team_name,
      'build-pipeline-name': pipeline_metadata.pipeline_name,
      'atc-external-url': concourse_cfg.external_url(),
    }

    # XXX deduplicate; mv to concourse package
    meta_dict['concourse_url'] = ci.util.urljoin(
        meta_dict['atc-external-url'],
        'teams',
        meta_dict['build-team-name'],
        'pipelines',
        meta_dict['build-pipeline-name'],
        'jobs',
        meta_dict['build-job-name'],
        'builds',
        meta_dict['build-name'],
    )

    # XXX do not hard-code env variables
    meta_dict['effective_version'] = os.environ.get('EFFECTIVE_VERSION')
    meta_dict['component_name'] = os.environ.get('COMPONENT_NAME')
    meta_dict['creation_date'] = datetime.datetime.now().isoformat()

    return meta_dict


class ElasticSearchClient:
    def __init__(
        self,
        elasticsearch: elasticsearch.Elasticsearch,
    ):
        self._api = elasticsearch

    def store_document(
        self,
        index: str,
        body: dict,
        inject_metadata=True,
        *args,
        **kwargs,
    ):
        ci.util.check_type(index, str)
        ci.util.check_type(body, dict)
        if 'doc_type' in kwargs:
            raise ValueError(
                '''
                doc_type attribute has been deprecated - see:
                https://www.elastic.co/guide/en/elasticsearch/reference/6.0/removal-of-types.html
                '''
            )

        if inject_metadata and _metadata_dict():
            md = _metadata_dict()
            body['cc_meta'] = md

        try:
            return self._api.index(
                index=index,
                doc_type='_doc',
                body=body,
                *args,
                **kwargs,
            )
        except elasticsearch.exceptions.RequestError as e:
            logger.error(e)
            logger.error(e.info.get('error').get('caused_by'))
            raise e

    def store_documents(
        self,
        index: str,
        body: [dict],
        inject_metadata=True,
        *args,
        **kwargs,
    ):
        # Bulk-loading uses a special format: A json specifying index name and doc-type
        # (always _doc) followed by the actual document json. These pairs (one for each document)
        # are then converted to newline delimited json

        # The index json does not change for bulk-loading into a single index.
        index_json = json.dumps({
            'index': {
                '_index': index,
                '_type': '_doc'
            }
        })
        return self.store_bulk(
            body='\n'.join([f'{index_json}\n{json.dumps(d)}' for d in body]),
            inject_metadata=inject_metadata,
            *args,
            **kwargs,
        )

    def store_bulk(
        self,
        body: str,
        inject_metadata=True,
        *args,
        **kwargs,
    ):
        ci.util.check_type(body, str)

        if inject_metadata and _metadata_dict():
            def inject_meta(line):
                parsed = json.loads(line)
                if 'index' not in parsed:
                    parsed['cc_meta'] = md
                    return json.dumps(parsed)
                return line

            md = _metadata_dict()
            patched_body = '\n'.join([inject_meta(line) for line in body.splitlines()])
            body = patched_body

        return self._api.bulk(
            body=body,
            *args,
            **kwargs,
        )


def metric_to_es(
    es_client: ElasticSearchClient,
    metric,
    index_name: str,
):
    try:
        es_client.store_document(
            index=index_name,
            body=dataclasses.asdict(metric),
            inject_metadata=False,
        )
    except Exception:
        import traceback
        logger.warning(traceback.format_exc())
        logger.warning('could not send route request to elastic search')
