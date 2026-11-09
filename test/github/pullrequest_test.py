import unittest.mock

import github3.exceptions
import pytest

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

def _make_repo(repo_id=1):
    repo = unittest.mock.MagicMock()
    repo.id = repo_id
    return repo


def _make_pr(
    title='[ci:component:github.com/gardener/foo:1.0.0->1.0.1]',
    author=gpr._BOT_AUTHOR,
    head_repo_id=1,
    base_repo_id=1,
    head_ref='ci-abcdefghi',
    state='open',
):
    pr = unittest.mock.MagicMock()
    pr.title = title
    pr.user.login = author
    pr.head.ref = head_ref
    pr.head.repo = _make_repo(head_repo_id) if head_repo_id is not None else None
    pr.base.repo = _make_repo(base_repo_id)
    pr.state = state
    return pr


def _iter(prs, base_repo_id=1):
    repo = _make_repo(base_repo_id)
    repo.pull_requests.return_value = prs
    return list(gpr.iter_upgrade_pullrequests(repository=repo))


def test_iter_accepts_bot_pr():
    results = _iter([_make_pr()])
    assert len(results) == 1


def test_iter_rejects_fork_pr():
    # head.repo.id differs from base repo id
    results = _iter([_make_pr(head_repo_id=99)])
    assert results == []


def test_iter_rejects_deleted_fork_pr():
    # head.repo is None when the fork was deleted
    results = _iter([_make_pr(head_repo_id=None)])
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
        _make_pr(head_repo_id=99),               # fork
        _make_pr(head_repo_id=None),             # deleted fork
        _make_pr(author='attacker'),             # non-bot
        _make_pr(title='[ci:bogus:x:1->2]'),    # bad title
    ]
    assert len(_iter(prs)) == 1


# ---------------------------------------------------------------------------
# purge — branch deletion guard
# ---------------------------------------------------------------------------

def _make_upr(head_repo_id=1, base_repo_id=1, head_ref='ci-abcdefghi'):
    pr = _make_pr(head_repo_id=head_repo_id, base_repo_id=base_repo_id, head_ref=head_ref)
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
    upr = _make_upr(head_repo_id=99)
    upr.purge()
    upr.pull_request.close.assert_called_once()
    upr.pull_request.repository.ref.assert_not_called()


def test_purge_skips_deletion_for_deleted_fork():
    upr = _make_upr(head_repo_id=None)
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
