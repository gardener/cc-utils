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

import dataclasses
import enum
import urllib.parse

import dacite

from concourse.model.job import AbortObsoleteJobs
from model.base import ModelBase


class EventBase(ModelBase):
    def repository(self):
        return Repository(self.raw['repository'])


class Repository(ModelBase):
    def github_host(self):
        return urllib.parse.urlparse(self.repository_url()).hostname

    def repository_url(self):
        url = self.raw['clone_url']
        if url.endswith('.git'):
            return url[:-4] # remove '.git' suffix
        return url

    def repository_path(self):
        return self.raw['full_name']


class RefType(enum.Enum):
    REPOSITORY = 'repository'
    BRANCH = 'branch'
    TAG = 'tag'


class CreateEvent(EventBase):
    def ref_type(self):
        return RefType(self.raw['ref_type'])

    def ref(self):
        '''
        @return: the ref's name or None if ref_type is repository
        '''
        return self.raw.get('ref', None)


class PushEvent(EventBase):
    def ref(self):
        return self.raw['ref']

    def modified_paths(self):
        # for now, only take head-commit into account
        # --> this could lead to missed updates
        head_commit = self.raw.get('head_commit', None)
        if not head_commit:
            return ()
        yield from head_commit.get('modified', ())

    def commit_message(self):
        # not all push events have a head_commit (e.g. push events sent on branch deletion)
        if head_commit := self.raw.get('head_commit'):
            return head_commit.get('message')
        return None

    def is_forced_push(self):
        return self.raw['forced']

    def previous_ref(self):
        return self.raw['before']


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
    READY_FOR_REVIEW = 'ready_for_review'


class PullRequestEvent(EventBase):
    def action(self):
        return PullRequestAction(self.raw['action'])

    def number(self):
        '''
        the PR number
        '''
        return self.raw['number']

    def label_names(self):
        return [
            label.get('name') for label in self.raw.get('pull_request').get('labels')
        ]

    def sender(self):
        '''
        the user who performed the event
        '''
        return self.raw['sender']


@dataclasses.dataclass
class Pipeline:
    pipeline_name: str
    target_team: str
    effective_definition: dict


@dataclasses.dataclass
class AbortConfig:
    abort_obsolete_jobs: AbortObsoleteJobs

    @staticmethod
    def from_dict(d: dict):
        return dacite.from_dict(
            data_class=AbortConfig,
            data=d,
            config=dacite.Config(cast=[AbortObsoleteJobs]),
        )
