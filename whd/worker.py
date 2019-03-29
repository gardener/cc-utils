# Copyright (c) 2019 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
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

from flask import Response
from flask_restful import Resource

import concourse.util
from model.webhook_dispatcher import WebhookDispatcherConfig


class WorkerResurrector(Resource):
    def __init__(self, whd_cfg: WebhookDispatcherConfig):
        self.whd_cfg = whd_cfg

    # called from Prometheus Alert Manager. Indicates that concourse worker(s) got restarted
    def post(self):
        concourse.util.prune_and_restart_concourse_worker(self.whd_cfg,)
        return Response(status=200)
