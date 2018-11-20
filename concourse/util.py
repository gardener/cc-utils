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

import concourse.client as concourse
import concourse.client.model


def list_github_resources(
    concourse_cfg,
    concourse_team: str='kubernetes',
    concourse_pipelines=None,
    github_url: str=None,
):
    concourse_api = concourse.from_cfg(concourse_cfg=concourse_cfg, team_name=concourse_team)
    pipeline_names = concourse_pipelines if concourse_pipelines else concourse_api.pipelines()
    yield from filter(
      lambda r: r.has_webhook_token(),
      concourse_api.pipeline_resources(pipeline_names=pipeline_names),
    )
