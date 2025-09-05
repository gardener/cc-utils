import enum

import dacite
import pytest

import release_notes.model as rnm
import release_notes.utils as rnu


@pytest.fixture
def release_notes_docs() -> list[rnm.ReleaseNotesDoc]:
    raw = [
        {
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
        },
        {
            "ocm": {
                "component_name": "github.com/gardener/dashboard",
                "component_version": "1.81.3"
            },
            "release_notes": [
                {
                    "audience": "operator",
                    "author": {
                        "hostname": "github.com",
                        "type": "githubUser",
                        "username": "gardener-ci-robot"
                    },
                    "category": "feature",
                    "contents": "This is a feature",
                    "mimetype": "text/markdown",
                    "reference": "[#2609](https://github.com/gardener/dashboard/pull/2609)",
                    "type": "standard"
                },
            ]
        },
    ]

    return [
        dacite.from_dict(
            data=rn,
            data_class=rnm.ReleaseNotesDoc,
            config=dacite.Config(
                cast=[enum.Enum],
            ),
        )
        for rn in raw
    ]


def test_release_notes_detail_filter(
    release_notes_docs: list[rnm.ReleaseNotesDoc],
):
    # this tests only validates filtering within a single release-note-doc
    release_notes_doc = release_notes_docs[0]

    def _filter(
        release_notes_doc: rnm.ReleaseNotesDoc,
        audiences: list[rnm.ReleaseNotesAudience] = [],
        categories: list[rnm.ReleaseNotesCategory] = [],
    ):
        return list(rnu.filter_release_notes(
            release_notes_docs=[release_notes_doc],
            audiences=audiences,
            categories=categories,
        ))[0].release_notes

    assert len(_filter(release_notes_doc)) == len(release_notes_doc.release_notes)
    assert len(_filter(release_notes_doc, audiences=[rnm.ReleaseNotesAudience.OPERATOR])) == 1
    assert len(_filter(release_notes_doc, categories=[rnm.ReleaseNotesCategory.BREAKING])) == 0
