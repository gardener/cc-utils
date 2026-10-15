import datetime
import enum
import unittest.mock as mock

import dacite
import pytest

import ocm
import release_notes.model as rnm
import release_notes.utils as rnu


@pytest.fixture
def release_notes_doc() -> rnm.ReleaseNotesDoc:
    raw = {
        "ocm": {
            "component_name": "github.com/gardener/gardener",
            "component_version": "v1.126.0"
        },
        "release_notes": [
            {
                "audience": "operator",
                "author": {
                    "hostname": "github.com",
                    "type": "githubUser",
                    "username": "gardener-ci-robot"
                },
                "category": "bugfix",
                "contents": "This is a bug",
                "mimetype": "text/markdown",
                "reference": "[#12798](https://github.com/gardener/gardener/pull/12798)",
                "type": "standard"
            },
            {
                "audience": "dependency",
                "author": {
                    "hostname": "github.com",
                    "type": "githubUser",
                    "username": "gardener-ci-robot"
                },
                "category": "other",
                "contents": "This is a dependency bump",
                "mimetype": "text/markdown",
                "reference": "[#12691](https://github.com/gardener/gardener/pull/12691)",
                "type": "standard"
            },
        ]
    }

    return dacite.from_dict(
        data=raw,
        data_class=rnm.ReleaseNotesDoc,
        config=dacite.Config(
            cast=[enum.Enum],
        ),
    )


def test_release_notes_detail_filter(
    release_notes_doc: rnm.ReleaseNotesDoc,
):
    default_include_all = rnu.filter_release_notes(release_notes_doc=release_notes_doc)
    assert len(default_include_all.release_notes) == len(release_notes_doc.release_notes)

    one_match = rnu.filter_release_notes(
        release_notes_doc=release_notes_doc,
        audiences=[rnm.ReleaseNotesAudience.OPERATOR],
    )
    assert len(one_match.release_notes) == 1

    filter_all = rnu.filter_release_notes(
        release_notes_doc=release_notes_doc,
        categories=[rnm.ReleaseNotesCategory.BREAKING],
    )
    assert len(filter_all.release_notes) == 0


def make_mock_commit(sha: str):
    commit = mock.MagicMock()
    commit.hexsha = sha
    return commit


def make_mock_pr(number: int, title: str, merged_at: datetime.datetime | None):
    pr = mock.MagicMock()
    pr.number = number
    pr.title = title
    pr.merged_at = merged_at
    pr.body = f'```other operator\nrelease note from PR {number}\n```'
    return pr


@pytest.fixture
def github_access():
    return ocm.GithubAccess(repoUrl='github.com/test/repo')


@pytest.fixture
def component():
    return ocm.Component(
        name='github.com/test/repo',
        version='v1.0.0',
        repositoryContexts=[],
        provider='test',
        sources=[],
        componentReferences=[],
        resources=[],
    )


def test_open_prs_not_included_in_release_notes(github_access, component):
    '''
    Regression test: GitHub's commits/{sha}/pulls API returns all open PRs whose base branch
    contains the commit — not just PRs merged into it. Only merged PRs should contribute
    release notes.
    '''
    merged_pr = make_mock_pr(
        number=100,
        title='fix: something',
        merged_at=datetime.datetime(2026, 9, 28, 10, 0, 0),
    )
    open_pr = make_mock_pr(
        number=101,
        title='feat: something else — open at release time',
        merged_at=None,
    )

    commit = make_mock_commit('aabbccdd' * 5)

    git_helper = mock.MagicMock()
    git_helper.repo.git.notes.side_effect = Exception('no notes')  # no cached notes

    github_api = mock.MagicMock()
    github_api_lookup = mock.MagicMock(return_value=github_api)

    with mock.patch(
        'release_notes.utils.list_associated_pulls',
        return_value=(merged_pr, open_pr),
    ), mock.patch(
        'release_notes.utils._find_git_notes_for_commit',
        return_value=None,
    ):
        result = rnu.request_pull_requests_from_api(
            git_helper=git_helper,
            github_api_lookup=github_api_lookup,
            github_access=github_access,
            commits=[commit],
            component=component,
            group_size=200,
            min_seconds_per_group=0,
        )

    prs_for_commit = result[commit.hexsha]
    pr_numbers = [pr.number for pr in prs_for_commit]

    assert 100 in pr_numbers, 'merged PR should be included in release notes'
    assert 101 not in pr_numbers, 'open PR must not be included in release notes'

    # verify the cached git note only contains the merged PR number, so that
    # the open PR cannot sneak back in via the pending path on future runs
    add_note_call = git_helper.add_note.call_args
    assert add_note_call is not None, 'git note should have been written'
    cached_body = add_note_call.kwargs['body']
    import yaml
    cached_docs = list(yaml.safe_load_all(cached_body))
    cached_prs = cached_docs[0]['meta']['data']['prs']
    assert 100 in cached_prs, 'merged PR should be in cached metadata'
    assert 101 not in cached_prs, 'open PR must not be in cached metadata'
