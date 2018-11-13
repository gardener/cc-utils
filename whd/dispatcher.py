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

import functools

from model.webhook_dispatcher import WebhookDispatcherConfig
import concourse.client
import util


class GithubWebhookDispatcher(object):
    def __init__(
        self,
        whd_cfg: WebhookDispatcherConfig
    ):
        self.whd_cfg = whd_cfg
        self.cfg_factory = util.ctx().cfg_factory()

    @functools.lru_cache()
    def concourse_clients(self):
        for concourse_job_mapping in self.whd_cfg.concourse_cfgs():
            concourse_cfg = self.cfg_factory.concourse(concourse_job_mapping.cfg_name())
            job_mapping_set = self.cfg_factory.job_mapping(concourse_job_mapping.job_mapping())

            for job_mapping in job_mapping_set.job_mappings().values():
                yield concourse.client.from_cfg(
                    concourse_cfg=concourse_cfg,
                    team_name=job_mapping.team_name(),
                )
