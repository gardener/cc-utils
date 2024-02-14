# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import json
import os

import ccc.elasticsearch
import ci.util


def store(index: str, body: str, cfg_name: str):
    elastic_cfg = ci.util.ctx().cfg_factory().elasticsearch(cfg_name)
    elastic_client = ccc.elasticsearch.from_cfg(elasticsearch_cfg=elastic_cfg)
    json_body = json.loads(body)

    result = elastic_client.store_document(
        index=index,
        body=json_body,
    )
    print(result)


def store_files(index: str, files: [str], cfg_name: str):
    elastic_cfg = ci.util.ctx().cfg_factory().elasticsearch(cfg_name)
    elastic_client = ccc.elasticsearch.from_cfg(elasticsearch_cfg=elastic_cfg)

    for file in files:
        ci.util.existing_file(file)

    for file in files:
        with open(file) as file_handle:
            json_body = json.load(file_handle)
            result = elastic_client.store_document(
                index=index,
                body=json_body,
                )
            print(result)


def store_bulk(file: str, cfg_name: str):
    elastic_cfg = ci.util.ctx().cfg_factory().elasticsearch(cfg_name)
    elastic_client = ccc.elasticsearch.from_cfg(elasticsearch_cfg=elastic_cfg)

    ci.util.existing_file(file)

    with open(file) as file_handle:
        result = elastic_client.store_bulk(
            body=file_handle.read()
        )
        print(result)


def store_dir(index: str, directory: ci.util.CliHints.existing_dir(), cfg_name: str):
    json_files = list()
    for (dirpath, dirnames, filenames) in os.walk(directory):
        for file in filenames:
            if file.endswith('.json'):
                json_files.append(os.path.join(dirpath, file))
    store_files(index, json_files, cfg_name)
