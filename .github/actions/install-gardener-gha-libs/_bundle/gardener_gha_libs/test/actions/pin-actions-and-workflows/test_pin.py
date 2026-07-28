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


def test_already_pinned_ref_unchanged():
    dep_prefix = '.github/actions/foo'
    original_uses = f'{_OWN_PREFIX}{dep_prefix}@{_SHA}'
    parsed = _make_parsed(original_uses)

    with unittest.mock.patch('pin._is_pinned', return_value=True):
        changed = pin._rewrite_parsed(
            parsed,
            own_org=_ORG,
            own_repo=_REPO,
            prefix_to_digest={dep_prefix: 'b' * 40},
            repo=None,
        )

    assert not changed
    uses = parsed['runs']['steps'][0]['uses']
    assert uses == original_uses


def test_extract_internal_deps_finds_dep():
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
