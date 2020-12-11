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
import functools

import ci.util
import concourse.client

from ensure import ensure_annotations


@functools.lru_cache()
@ensure_annotations
def client_from_cfg_name(concourse_cfg_name: str, team_name: str):
    cfg_factory = ci.util.ctx().cfg_factory()
    cc_cfg = cfg_factory.concourse(concourse_cfg_name)
    return concourse.client.from_cfg(
        concourse_cfg=cc_cfg,
        team_name=team_name,
        verify_ssl=True,
    )
