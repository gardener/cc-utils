# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import os
import sys
import unittest.mock

# test/__init__.py already adds repo_root to sys.path;
# add the action directory so pin is importable
sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', '..', '..', '.github', 'actions',
                     'pin-actions-and-workflows')
    ),
)

import pin


_SHA = 'a' * 40
_SHA2 = 'b' * 40
_ORG = 'myorg'
_REPO = 'myrepo'
_OWN_PREFIX = f'{_ORG}/{_REPO}/'


def _make_parsed(uses_value: str) -> dict:
    return {
        'runs': {
            'using': 'composite',
            'steps': [{'uses': uses_value}],
        }
    }


def test_rewrite_unpinned_ref():
    dep_prefix = '.github/actions/foo'
    parsed = _make_parsed(f'{_OWN_PREFIX}{dep_prefix}@master')

    with unittest.mock.patch('pin._is_pinned', return_value=False):
        changed = pin._rewrite_parsed(
            parsed,
            own_org=_ORG,
            own_repo=_REPO,
            prefix_to_digest={dep_prefix: _SHA},
            repo=None,
        )

    assert changed
    uses = parsed['runs']['steps'][0]['uses']
    assert uses == f'{_OWN_PREFIX}{dep_prefix}@{_SHA}'


def test_already_pinned_ref_at_current_sha_unchanged():
    '''A pinned internal ref already at the current digest must not be touched.'''
    dep_prefix = '.github/actions/foo'
    original_uses = f'{_OWN_PREFIX}{dep_prefix}@{_SHA}'
    parsed = _make_parsed(original_uses)

    with unittest.mock.patch('pin._is_pinned', return_value=True):
        changed = pin._rewrite_parsed(
            parsed,
            own_org=_ORG,
            own_repo=_REPO,
            prefix_to_digest={dep_prefix: _SHA},  # same SHA — already current
            repo=None,
        )

    assert not changed
    assert parsed['runs']['steps'][0]['uses'] == original_uses


def test_stale_pinned_internal_ref_is_updated():
    '''
    Regression: if a caller already has a pinned SHA for an internal dep, but that
    dep has since been updated (new SHA in prefix_to_digest), the caller must be
    rewritten to the new SHA.  Previously _rewrite_parsed skipped all pinned refs
    unconditionally, leaving callers pointing at stale SHAs.
    '''
    dep_prefix = '.github/actions/foo'
    old_sha = _SHA
    new_sha = _SHA2
    parsed = _make_parsed(f'{_OWN_PREFIX}{dep_prefix}@{old_sha}')

    # old_sha is a valid/existing commit (pinned), new_sha is the updated digest
    def _is_pinned_side_effect(ref, repo):
        return ref == old_sha

    with unittest.mock.patch('pin._is_pinned', side_effect=_is_pinned_side_effect):
        changed = pin._rewrite_parsed(
            parsed,
            own_org=_ORG,
            own_repo=_REPO,
            prefix_to_digest={dep_prefix: new_sha},
            repo=None,
        )

    assert changed, 'stale pinned internal ref should have been updated'
    assert parsed['runs']['steps'][0]['uses'] == f'{_OWN_PREFIX}{dep_prefix}@{new_sha}'


def test_stale_pinned_external_ref_not_touched():
    '''External (non-own-repo) pinned refs must never be rewritten.'''
    parsed = _make_parsed(f'actions/checkout@{_SHA}')

    with unittest.mock.patch('pin._is_pinned', return_value=True):
        changed = pin._rewrite_parsed(
            parsed,
            own_org=_ORG,
            own_repo=_REPO,
            prefix_to_digest={},
            repo=None,
        )

    assert not changed
    assert parsed['runs']['steps'][0]['uses'] == f'actions/checkout@{_SHA}'


def test_extract_internal_deps_finds_unpinned_dep():
    dep_prefix = '.github/actions/bar'
    parsed = _make_parsed(f'{_OWN_PREFIX}{dep_prefix}@master')

    with unittest.mock.patch('pin._is_pinned', return_value=False):
        deps = list(pin._extract_internal_deps(
            parsed,
            own_org=_ORG,
            own_repo=_REPO,
            known_prefixes={dep_prefix},
            repo=None,
        ))

    assert deps == [dep_prefix]


def test_extract_internal_deps_finds_pinned_dep():
    '''
    Regression: internal deps referenced via an already-pinned SHA must still be
    included in the dependency graph so topological ordering is correct and
    _rewrite_parsed gets a chance to update them.
    '''
    dep_prefix = '.github/actions/bar'
    parsed = _make_parsed(f'{_OWN_PREFIX}{dep_prefix}@{_SHA}')

    with unittest.mock.patch('pin._is_pinned', return_value=True):
        deps = list(pin._extract_internal_deps(
            parsed,
            own_org=_ORG,
            own_repo=_REPO,
            known_prefixes={dep_prefix},
            repo=None,
        ))

    assert deps == [dep_prefix], (
        'pinned internal dep must be included in graph so callers are updated when dep changes'
    )
