# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import enum
import urllib.parse

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
