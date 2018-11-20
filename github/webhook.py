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


class WebhookQueryAttributes(object):
    WEBHOOK_TOKEN_ATTRIBUTE_NAME = 'webhook_token'
    CONCOURSE_ID_ATTRIBUTE_NAME = 'concourse_id'
    JOB_MAPPING_ID_ATTRIBUTE_NAME = 'job_mapping_id'

    def __init__(
        self,
        webhook_token: str,
        concourse_id: str,
        job_mapping_id: str,
    ):
        self.webhook_token = webhook_token
        self.concourse_id = concourse_id
        self.job_mapping_id = job_mapping_id
