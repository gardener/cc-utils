# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

'''
Check that no workflow or action file in .github/ contains a pinned SHA reference
to an internal (own-repo) action or workflow.

On master, all internal uses: references must use @master (or another mutable
branch ref), not a hardcoded commit SHA.  Pinned SHAs belong exclusively on the
v1 branch, where the pin-actions-and-workflows tool writes them.

If a specific reference must legitimately be pinned on master, append
a `# pin-allow` comment to that line.

This test is only meaningful on master; it is skipped on other refs.
'''

import os
import sys

import pytest

sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', '..', '..', '.github', 'actions',
                     'pin-actions-and-workflows')
    ),
)

import pin

_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..')
)
_OWN_ORG = 'gardener'
_OWN_REPO = 'cc-utils'


def _current_ref() -> str:
    '''
    Return the current branch name.  Prefers the GITHUB_REF_NAME env var (set
    by GitHub Actions even for detached-HEAD checkouts) over git symbolic-ref.
    '''
    import subprocess
    # GitHub Actions sets this for both push and pull_request events
    if ref := os.environ.get('GITHUB_REF_NAME'):
        return ref
    result = subprocess.run(
        ['git', 'symbolic-ref', '--short', 'HEAD'],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return subprocess.check_output(
        ['git', 'rev-parse', '--short', 'HEAD'],
        text=True,
        cwd=_REPO_ROOT,
    ).strip()


def _scan_violations() -> list[tuple[str, str]]:
    '''
    Return list of (file_path, uses_value) for every internal uses: reference
    that is pinned to a SHA rather than a mutable branch ref.
    '''
    import glob
    import ruamel.yaml as _yaml

    _y = _yaml.YAML()
    _y.preserve_quotes = True

    own_prefix = f'{_OWN_ORG}/{_OWN_REPO}/'
    import git as gitpython
    repo = gitpython.Repo(_REPO_ROOT)

    violations = []
    patterns = [
        os.path.join(_REPO_ROOT, '.github', 'actions', '**', 'action.yaml'),
        os.path.join(_REPO_ROOT, '.github', 'actions', '**', 'action.yml'),
        os.path.join(_REPO_ROOT, '.github', 'workflows', '*.yaml'),
        os.path.join(_REPO_ROOT, '.github', 'workflows', '*.yml'),
    ]
    for pattern in patterns:
        for fpath in glob.glob(pattern, recursive=True):
            with open(fpath) as f:
                raw = f.read()
            parsed = _y.load(raw)
            if not isinstance(parsed, dict):
                continue
            rel = os.path.relpath(fpath, _REPO_ROOT)
            for step, key in pin._iter_step_uses(parsed):
                uses_value = step[key]
                if not isinstance(uses_value, str):
                    continue
                if not uses_value.startswith(own_prefix):
                    continue
                after_repo = uses_value[len(own_prefix):]
                if '@' not in after_repo:
                    continue
                _, ref = after_repo.rsplit('@', 1)
                if not pin._is_pinned(ref, repo):
                    continue
                # check for explicit exemption comment on this line
                # ruamel.yaml stores end-of-line comments on the value's column object
                comment = None
                try:
                    ca = step.ca  # CommentedMap attribute
                    if ca and ca.items.get(key):
                        comment_token = ca.items[key][2]  # end-of-value comment
                        if comment_token:
                            comment = comment_token.value
                except Exception:
                    pass
                if comment and 'pin-allow' in comment:
                    continue
                violations.append((rel, uses_value))

    return violations


def test_no_pinned_internal_refs_on_master():
    ref = _current_ref()
    if ref != 'master':
        pytest.skip(f'only enforced on master (current ref: {ref})')

    violations = _scan_violations()
    if violations:
        lines = '\n'.join(f'  {path}: {uses}' for path, uses in violations)
        pytest.fail(
            f'Found {len(violations)} internal uses: reference(s) with pinned SHAs on master.\n'
            f'Internal refs must use @master. Add "# pin-allow" to exempt intentional pins.\n'
            f'\n{lines}'
        )
