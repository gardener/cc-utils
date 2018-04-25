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
import os

from util import (
    parse_yaml_file
)
from model import JobMapping

class PipelineEnumerator(object):
    def __init__(self, base_dir, cfg_set):
        self.base_dir = base_dir
        self.cfg_set = cfg_set

    def enumerate_pipeline_definitions(self, job_mapping: JobMapping):
        for pd in enumerate_pipeline_definitions(
                [os.path.join(self.base_dir, d) for d in job_mapping.definition_dirs()]
        ):
            yield pd

def enumerate_pipeline_definitions(directories):
    for directory in directories:
        # for now, hard-code mandatory .repository_mapping
        repo_mapping = parse_yaml_file(os.path.join(directory, '.repository_mapping'))
        repo_definition_mapping = {repo_path: list() for repo_path in repo_mapping.keys()}

        for repo_path, definition_files in repo_mapping.items():
            for definition_file_path in definition_files:
                abs_file = os.path.abspath(os.path.join(directory, definition_file_path))
                pipeline_raw_definition = parse_yaml_file(abs_file, as_snd=False)
                repo_definition_mapping[repo_path].append(pipeline_raw_definition)

        yield repo_definition_mapping.items()
