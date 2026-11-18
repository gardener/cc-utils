import unittest.mock

import github3.exceptions
import pytest

import github.commit
import github.pullrequest as gpr


def test_split_chunks_if_too_long():
    assert gpr.split_into_chunks_if_too_long(
        string='abcd',
        split_hint='does-not-matter',
        max_leng=100,
        max_chunk_leng=100,
    ) == ('abcd', ())

    assert gpr.split_into_chunks_if_too_long(
        string='12345678',
        split_hint='X',
        max_leng=2,
        max_chunk_leng=3,
    ) == ('1X', ('234', '567', '8'))


# ---------------------------------------------------------------------------
# parse_pullrequest_title — title regex
# ---------------------------------------------------------------------------

def _parse(title, invalid_ok=False):
    return gpr.parse_pullrequest_title(title=title, invalid_ok=invalid_ok)


def test_parse_component_title():
    result = _parse('[ci:component:github.com/gardener/etcd-wrapper:v0.8.3->v0.8.4]')
    assert result.component_name == 'github.com/gardener/etcd-wrapper'
    assert result.whence.version == 'v0.8.3'
    assert result.whither.version == 'v0.8.4'


def test_parse_semver_versions():
    result = _parse('[ci:component:github.com/gardener/foo:1.2.3->1.2.4]')
    assert result.whence.version == '1.2.3'
    assert result.whither.version == '1.2.4'


def test_parse_invalid_kind_raises():
    with pytest.raises(ValueError):
        _parse('[ci:bogus:x:1->2]')


def test_parse_invalid_kind_invalid_ok():
    assert _parse('[ci:bogus:x:1->2]', invalid_ok=True) is None


def test_parse_unknown_reference_name_raises():
    # 'name' kind without a reference_component must raise
    with pytest.raises(ValueError):
        _parse('[ci:name:some-ref:1->2]')


def test_parse_extra_colon_in_versions_rejected():
    # extra colon in the version field must not parse as a valid title
    assert _parse('[ci:component:github.com/foo:1:0->2:0]', invalid_ok=True) is None


def test_parse_extra_arrow_rejected():
    assert _parse('[ci:component:github.com/foo:1->2->3]', invalid_ok=True) is None


def test_parse_apostrophe_in_title_rejected():
    # "won't fix" style label used as a title must not parse
    assert _parse("[ci:component:github.com/foo:won't->fix]", invalid_ok=True) is None


def test_parse_empty_version_rejected():
    assert _parse('[ci:component:github.com/foo:->1.0]', invalid_ok=True) is None
    assert _parse('[ci:component:github.com/foo:1.0->]', invalid_ok=True) is None


def test_parse_no_brackets_rejected():
    assert _parse('ci:component:github.com/foo:1->2', invalid_ok=True) is None


# ---------------------------------------------------------------------------
# iter_upgrade_pullrequests — fork / author filter
# ---------------------------------------------------------------------------

def _make_repo(owner='myorg', name='myrepo'):
    repo = unittest.mock.MagicMock()
    repo.owner.login = owner
    repo.name = name
    return repo


def _make_pr(
    title='[ci:component:github.com/gardener/foo:1.0.0->1.0.1]',
    author=gpr._BOT_AUTHOR,
    is_fork=False,
    head_deleted=False,
    head_ref='ci-abcdefghi',
    state='open',
):
    pr = unittest.mock.MagicMock()
    pr.title = title
    pr.user.login = author
    pr.head.ref = head_ref
    pr.state = state
    # github3 sets head.repo to an (owner, name) tuple; a deleted fork yields ('', '')
    if head_deleted:
        pr.head.repo = ('', '')
    elif is_fork:
        pr.head.repo = ('attacker', 'myrepo')
    else:
        pr.head.repo = ('myorg', 'myrepo')
    return pr


def _iter(prs, owner='myorg', name='myrepo'):
    repo = _make_repo(owner=owner, name=name)
    repo.pull_requests.return_value = prs
    return list(gpr.iter_upgrade_pullrequests(repository=repo))


def test_iter_accepts_bot_pr():
    results = _iter([_make_pr()])
    assert len(results) == 1


def test_iter_rejects_fork_pr():
    results = _iter([_make_pr(is_fork=True)])
    assert results == []


def test_iter_rejects_deleted_fork_pr():
    results = _iter([_make_pr(head_deleted=True)])
    assert results == []


def test_iter_rejects_non_bot_author():
    results = _iter([_make_pr(author='some-user')])
    assert results == []


def test_iter_rejects_invalid_title():
    results = _iter([_make_pr(title='[ci:bogus:x:1->2]')])
    assert results == []


def test_iter_multiple_mixed():
    prs = [
        _make_pr(),                              # good
        _make_pr(is_fork=True),                  # fork
        _make_pr(head_deleted=True),             # deleted fork
        _make_pr(author='attacker'),             # non-bot
        _make_pr(title='[ci:bogus:x:1->2]'),    # bad title
    ]
    assert len(_iter(prs)) == 1


# ---------------------------------------------------------------------------
# purge — branch deletion guard
# ---------------------------------------------------------------------------

def _make_upr(is_fork=False, head_deleted=False, head_ref='ci-abcdefghi'):
    pr = _make_pr(is_fork=is_fork, head_deleted=head_deleted, head_ref=head_ref)
    # purge() checks head.repo against base.repo via the tuple; align them
    pr.base.repo = ('myorg', 'myrepo')
    upr = gpr.UpgradePullRequest(
        pull_request=pr,
        upgrade_vector=unittest.mock.MagicMock(),
    )
    return upr


def test_purge_deletes_bot_branch():
    upr = _make_upr()
    upr.purge()
    upr.pull_request.close.assert_called_once()
    upr.pull_request.repository.ref.assert_called_once_with('heads/ci-abcdefghi')
    upr.pull_request.repository.ref.return_value.delete.assert_called_once()


def test_purge_skips_deletion_for_fork():
    upr = _make_upr(is_fork=True)
    upr.purge()
    upr.pull_request.close.assert_called_once()
    upr.pull_request.repository.ref.assert_not_called()


def test_purge_skips_deletion_for_deleted_fork():
    upr = _make_upr(head_deleted=True)
    upr.purge()
    upr.pull_request.close.assert_called_once()
    upr.pull_request.repository.ref.assert_not_called()


def test_purge_skips_deletion_for_non_bot_branch():
    # a branch name that doesn't match ^ci-[a-z]{9}$
    upr = _make_upr(head_ref='release-v0.39')
    upr.purge()
    upr.pull_request.close.assert_called_once()
    upr.pull_request.repository.ref.assert_not_called()


def _make_github3_exc(exc_class, status_code):
    resp = unittest.mock.MagicMock()
    resp.status_code = status_code
    return exc_class(resp)


def test_purge_handles_not_found_gracefully():
    upr = _make_upr()
    upr.pull_request.repository.ref.return_value.delete.side_effect = _make_github3_exc(
        github3.exceptions.NotFoundError, 404,
    )
    upr.purge()  # must not raise
    upr.pull_request.close.assert_called_once()


def test_purge_propagates_other_errors():
    upr = _make_upr()
    upr.pull_request.repository.ref.return_value.delete.side_effect = _make_github3_exc(
        github3.exceptions.ForbiddenError, 403,
    )
    with pytest.raises(github3.exceptions.ForbiddenError):
        upr.purge()


# ---------------------------------------------------------------------------
# commit_and_push_to_tmp_branch — signed-commit creation via the GitHub-API
# ---------------------------------------------------------------------------

import os
import subprocess

import git

import gitutil


def _init_repo(path):
    def g(*args):
        subprocess.run(('git', '-C', path, *args), check=True, capture_output=True, text=True)
    g('init', '-q')
    g('config', 'user.email', 't@example.org')
    g('config', 'user.name', 'tester')
    g('config', 'commit.gpgsign', 'false')
    with open(os.path.join(path, 'VERSION'), 'w') as f:
        f.write('1.0.0\n')
    g('add', '-A')
    g('commit', '-qm', 'init')


def test_commit_and_push_creates_signed_commit_and_ref(tmp_path):
    repo_dir = str(tmp_path)
    _init_repo(repo_dir)
    repo = git.Repo(repo_dir)
    parent_sha = repo.head.commit.hexsha

    # produce a worktree-diff (modify tracked + add untracked)
    with open(os.path.join(repo_dir, 'VERSION'), 'w') as f:
        f.write('2.0.0\n')
    with open(os.path.join(repo_dir, 'NEW.txt'), 'w') as f:
        f.write('new\n')

    git_helper = gitutil.GitHelper(repo=repo, git_cfg=unittest.mock.Mock())

    repository = unittest.mock.Mock()

    with unittest.mock.patch.object(
        github.commit, 'create_signed_commit', return_value='signed-sha',
    ) as create_signed_commit:
        with gpr.commit_and_push_to_tmp_branch(
            repository=repository,
            git_helper=git_helper,
            commit_message='Upgrade foo',
        ) as branch_name:
            assert gpr._BOT_BRANCH_RE.match(branch_name)

    # signed commit created via the API against the local HEAD as parent
    create_signed_commit.assert_called_once()
    _, kwargs = create_signed_commit.call_args
    assert kwargs['parent_sha'] == parent_sha
    assert kwargs['message'] == 'Upgrade foo'
    changed_paths = {fc.path for fc in kwargs['files']}
    assert changed_paths == {'VERSION', 'NEW.txt'}

    # branch-ref points at the signed commit; nothing pushed via git
    repository.create_ref.assert_called_once_with(
        f'refs/heads/{branch_name}', 'signed-sha',
    )


def test_commit_and_push_rejects_empty_worktree(tmp_path):
    repo_dir = str(tmp_path)
    _init_repo(repo_dir)
    repo = git.Repo(repo_dir)
    git_helper = gitutil.GitHelper(repo=repo, git_cfg=unittest.mock.Mock())

    with pytest.raises(RuntimeError):
        with gpr.commit_and_push_to_tmp_branch(
            repository=unittest.mock.Mock(),
            git_helper=git_helper,
            commit_message='noop',
        ):
            pass


def test_commit_and_push_purges_branch_on_body_exception(tmp_path):
    repo_dir = str(tmp_path)
    _init_repo(repo_dir)
    repo = git.Repo(repo_dir)
    with open(os.path.join(repo_dir, 'VERSION'), 'w') as f:
        f.write('2.0.0\n')
    git_helper = gitutil.GitHelper(repo=repo, git_cfg=unittest.mock.Mock())
    repository = unittest.mock.Mock()

    with unittest.mock.patch.object(
        github.commit, 'create_signed_commit', return_value='signed-sha',
    ):
        with pytest.raises(ValueError):
            with gpr.commit_and_push_to_tmp_branch(
                repository=repository,
                git_helper=git_helper,
                commit_message='x',
            ) as branch_name:
                raise ValueError('boom')

    repository.ref.assert_called_with(f'heads/{branch_name}')
    repository.ref.return_value.delete.assert_called()
