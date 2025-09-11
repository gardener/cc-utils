import enum

import dacite
import pytest

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
