# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import os
import sys

sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', '..', '..', '.github', 'actions', 'params')
    ),
)

import params


def test_registry_urls_snapshot():
    urls = params.compute_registry_urls(
        mode='snapshot',
        prefix='my.registry/project',
        snapshots_suffix='snapshots',
        releases_suffix='releases',
    )
    assert urls.oci_registry == 'my.registry/project/snapshots'
    assert urls.ocm_repository == 'my.registry/project/snapshots'
    assert urls.ocm_releases_repository == 'my.registry/project/releases'
    # releases repo always appended to ocm_repositories
    assert 'my.registry/project/releases' in urls.ocm_repositories


def test_registry_urls_release():
    urls = params.compute_registry_urls(
        mode='release',
        prefix='my.registry/project',
        snapshots_suffix='snapshots',
        releases_suffix='releases',
    )
    assert urls.oci_registry == 'my.registry/project/releases'
    assert urls.ocm_repository == 'my.registry/project/releases'


def test_registry_urls_ocm_repositories_appends_releases():
    urls = params.compute_registry_urls(
        mode='snapshot',
        prefix='my.registry/project',
        snapshots_suffix='snapshots',
        releases_suffix='releases',
        ocm_repositories='other.registry/foo',
    )
    assert urls.ocm_repositories == 'other.registry/foo,my.registry/project/releases'


def test_registry_urls_ocm_repositories_no_duplicate():
    releases = 'my.registry/project/releases'
    urls = params.compute_registry_urls(
        mode='snapshot',
        prefix='my.registry/project',
        snapshots_suffix='snapshots',
        releases_suffix='releases',
        ocm_repositories=releases,
    )
    assert urls.ocm_repositories.count(releases) == 1


def test_can_push_false_for_pr_from_fork():
    can_push, is_pr_from_fork = params.determine_can_push(
        event_name='pull_request',
        repo_owner='owner-org',
        head_owner='fork-user',
    )
    assert can_push.value is False
    assert is_pr_from_fork is True
    assert can_push.reason


def test_can_push_true_for_pr_from_same_owner():
    can_push, is_pr_from_fork = params.determine_can_push(
        event_name='pull_request',
        repo_owner='owner-org',
        head_owner='owner-org',
    )
    assert can_push.value is True
    assert is_pr_from_fork is False


def test_can_push_pull_request_target_trusted_association():
    for association in ('COLLABORATOR', 'MEMBER', 'OWNER'):
        can_push, _ = params.determine_can_push(
            event_name='pull_request_target',
            author_association=association,
        )
        assert can_push.value is True, association


def test_can_push_pull_request_target_trusted_label_overrides():
    can_push, _ = params.determine_can_push(
        event_name='pull_request_target',
        author_association='CONTRIBUTOR',
        event_action='labeled',
        trusted_label='ok-to-test',
        event_label='ok-to-test',
    )
    assert can_push.value is True
    assert 'ok-to-test' in can_push.reason


def test_can_push_false_for_long_ref():
    can_push, _ = params.determine_can_push(
        event_name='workflow_dispatch',
        ref='refs/heads/' + 'x' * 50,
    )
    assert can_push.value is False
    assert 'ref' in can_push.reason
