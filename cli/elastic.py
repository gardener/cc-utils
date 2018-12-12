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

import json

import os
import ccc.elasticsearch
import util


meta_dir = './meta'


def store(index: str, body: str, cfg_name: str):
    elastic_cfg = util.ctx().cfg_factory().elasticsearch(cfg_name)
    elastic_client = ccc.elasticsearch.from_cfg(elasticsearch_cfg=elastic_cfg)
    json_body = json.load(body)

    try:
        meta = get_meta()
    except RuntimeError:
        util.warning("Could not read metadata")
        meta = dict()

    json_body.update(meta)

    result = elastic_client.store_document(
        index=index,
        body=json_body,
    )

    print(result)


def store_files(index: str, files: [str], cfg_name: str):
    elastic_cfg = util.ctx().cfg_factory().elasticsearch(cfg_name)
    elastic_client = ccc.elasticsearch.from_cfg(elasticsearch_cfg=elastic_cfg)

    for file in files:
        util.existing_file(file)

    try:
        meta = get_meta()
    except RuntimeError:
        util.warning("Could not read metadata")
        meta = dict()

    for file in files:
        with open(file) as file_handle:
            json_body = json.load(file_handle)
            json_body['cc_meta'] = meta
            result = elastic_client.store_document(
                index=index,
                body=json_body,
                )
            print(result)


def store_dir(index: str, directory: util.CliHints.existing_dir(), cfg_name: str):
    json_files = list()
    for (dirpath, dirnames, filenames) in os.walk(directory):
        for file in filenames:
            if file.endswith('.json'):
                json_files.append(os.path.join(dirpath, file))
    store_files(index, json_files, cfg_name)


def get_meta():
    meta = dict()
    if not os.path.isdir(meta_dir):
        raise RuntimeError()
    for (dirpath, dirnames, filenames) in os.walk(meta_dir):
        for file in filenames:
            key = file
            value = ""
            with open(os.path.join(dirpath, file)) as file_handle:
                for line in file_handle.readlines():
                    value += line
            meta[key] = value
    # calculate concourse url of corresponding build
    meta['concourse_url'] = "/".join((
        meta['atc-external-url'],
        'teams',
        meta['build-team-name'],
        'pipelines',
        meta['build-pipeline-name'],
        'jobs',
        meta['build-job-name'],
        'builds',
        meta['build-name'],
        ))
    return meta
