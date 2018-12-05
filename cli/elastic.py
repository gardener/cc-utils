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


def store(index: str, body: str, cfg_name: str):
    elastic_cfg = util.ctx().cfg_factory().elasticsearch(cfg_name)
    elastic_client = ccc.elasticsearch.from_cfg(elasticsearch_cfg=elastic_cfg)

    result = elastic_client.store_document(
        index=index,
        body=json.loads(body),
    )

    print(result)


def store_files(index: str, files: [str], cfg_name: str):
    elastic_cfg = util.ctx().cfg_factory().elasticsearch(cfg_name)
    elastic_client = ccc.elasticsearch.from_cfg(elasticsearch_cfg=elastic_cfg)

    for f in files:
        util.existing_file(f)

    for f in files:
        with open(f) as fh:
            print(elastic_client.store_document(
                index=index,
                body=json.load(fh),
                )
            )


def store_dir(index: str, directory: util.CliHints.existing_dir(), cfg_name: str):
    json_files = list()
    for (dirpath, dirnames, filenames) in os.walk(directory):
        for file in filenames:
            if file.endswith('.json'):
                json_files.append(os.path.join(dirpath, file))
    store_files(index, json_files, cfg_name)
