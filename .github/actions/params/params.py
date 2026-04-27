#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import dataclasses


@dataclasses.dataclass
class RegistryUrls:
    oci_registry: str
    ocm_repository: str
    ocm_releases_repository: str
    ocm_repositories: str


@dataclasses.dataclass
class CanPush:
    value: bool
    reason: str


_TRUSTED_AUTHOR_ASSOCIATIONS = ('COLLABORATOR', 'MEMBER', 'OWNER')


def compute_registry_urls(
    mode: str,
    prefix: str,
    snapshots_suffix: str,
    releases_suffix: str,
    ocm_repositories: str = '',
) -> RegistryUrls:
    '''
    Computes OCI/OCM target URLs from mode and registry prefix/suffix inputs.
    ocm_repositories is an optional comma-separated list; the releases repository
    is always appended if not already present.
    '''
    prefix = prefix.rstrip('/')
    ocm_releases_repository = f'{prefix}/{releases_suffix}'

    if not ocm_repositories:
        ocm_repositories = ocm_releases_repository
    elif ocm_releases_repository not in ocm_repositories:
        ocm_repositories = f'{ocm_repositories},{ocm_releases_repository}'

    if mode == 'snapshot':
        ocm_repository = f'{prefix}/{snapshots_suffix}'
        oci_registry = f'{prefix}/{snapshots_suffix}'
    elif mode == 'release':
        ocm_repository = ocm_releases_repository
        oci_registry = f'{prefix}/{releases_suffix}'
    else:
        raise ValueError(f'unknown mode: {mode!r}')

    return RegistryUrls(
        oci_registry=oci_registry,
        ocm_repository=ocm_repository,
        ocm_releases_repository=ocm_releases_repository,
        ocm_repositories=ocm_repositories,
    )


def determine_can_push(
    event_name: str,
    repo_owner: str = '',
    head_owner: str = '',
    author_association: str = '',
    event_action: str = '',
    trusted_label: str = 'ok-to-test',
    event_label: str = '',
    ref: str = '',
) -> CanPush:
    '''
    Determines whether the current workflow run is allowed to push artefacts.

    Rules (in order):
    1. pull_request from a different owner → cannot push
    2. pull_request from same owner → can push
    3. pull_request_target with trusted author_association → can push;
       if labeled with trusted_label → can push regardless of association
    4. workflow_dispatch → can push
    5. anything else → can push (unknown/default)
    6. ref longer than 50 chars → cannot push (GAR workaround)
    '''
    is_pr_from_fork = False
    can_push = True
    reason = 'unknown'

    if event_name == 'pull_request':
        if repo_owner != head_owner:
            is_pr_from_fork = True
            can_push = False
            reason = 'untrusted fork'
        else:
            reason = 'pullrequest from local branch'

    elif event_name == 'pull_request_target':
        if author_association in _TRUSTED_AUTHOR_ASSOCIATIONS:
            can_push = True
            reason = 'trusted fork'
        else:
            can_push = False
            reason = 'untrusted fork'

        if event_action == 'labeled' and not can_push:
            if event_label == trusted_label:
                can_push = True
                reason = f'label {trusted_label} present'

    elif event_name == 'workflow_dispatch':
        can_push = True
        reason = 'run was triggered manually'

    else:
        is_pr_from_fork = False
        can_push = True
        reason = 'unknown'

    # workaround: https://issuetracker.google.com/issues/264362370?pli=1
    if ref and len(ref) > 50:
        can_push = False
        reason = 'ref exceeds allowed length of 50 chars'

    return CanPush(value=can_push, reason=reason), is_pr_from_fork
