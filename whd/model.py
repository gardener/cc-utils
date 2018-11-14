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

import enum
import urllib.parse

from model.base import ModelBase


class EventBase(ModelBase):
    def repository(self):
        return Repository(self.raw['repository'])


class Repository(ModelBase):
    def github_host(self):
        return urllib.parse.urlparse(self.raw['clone_url']).hostname

    def repository_path(self):
        return self.raw['full_name']


class PushEvent(EventBase):
    def ref(self):
        return self.raw['ref']


class PullRequestAction(enum.Enum):
    ASSIGNED = 'assigned'
    UNASSIGNED = 'unassigned'
    REVIEW_REQUESTED = 'review_requested'
    REVIEW_REQUEST_REMOVED = 'review_request_removed'
    LABELED = 'labeled'
    UNLABELED = 'unlabeled'
    OPENED = 'opened'
    EDITED = 'edited'
    CLOSED = 'closed'
    REOPENED = 'reopened'
    SYNCHRONIZE = 'synchronize'


class PullRequestEvent(EventBase):
    def action(self):
        return PullRequestAction(self.raw['action'])
