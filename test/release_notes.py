import os
import tempfile

import ocm
import concourse.steps.release
import release_notes.model as rnm


class DummyGitHelper:
    def __init__(
        self,
        repo_path='.'
    ):
        self.repo = None
        self.repo_path = repo_path


class DummyComponent:
    def __init__(
        self,
        name='github.com/gardener/cc-utils',
        version='1.0.0'
    ):
        self.name = name
        self.version = version
        self.resources = []


class DummyComponentDescriptor:
    def __init__(
        self,
        component
    ):
        self.component = component


def create_dummy_notes():
    note_entry = rnm.ReleaseNoteEntry(
        mimetype='text/markdown',
        contents='Fix dummy bug',
        category=rnm.ReleaseNotesCategory.BUGFIX,
        audience=rnm.ReleaseNotesAudience.DEVELOPER,
        author=rnm.ReleaseNotesAuthor(
            hostname='github.com',
            username='dummyuser',
        ),
        pullrequest='https://github.com/example/pr/1'
    )

    note_doc = rnm.ReleaseNotesDoc(
        ocm=rnm.ReleaseNotesOcmRef(
            component_name='github.com/gardener/cc-utils',
            component_version='1.0.0',
        ),
        release_notes=[note_entry],
    )

    return [note_doc]


def test_collect_release_notes_end_to_end(monkeypatch):
    component = DummyComponent()
    component_descriptor = DummyComponentDescriptor(component)

    monkeypatch.setattr(
        'release_notes.fetch.fetch_release_notes',
        lambda *args, **kwargs: create_dummy_notes()
    )

    monkeypatch.setattr(
        'release_notes.ocm.release_notes_range_recursive',
        lambda *args, **kwargs: [
            (ocm.ComponentIdentity(name=component.name, version=component.version),
            'Subcomponent Fix: Something was fixed')
        ]
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = os.path.join(tmpdir, 'release-notes-archive.tar')

        release_notes_md, full_release_notes_md = concourse.steps.release.collect_release_notes(
            git_helper=DummyGitHelper(),
            release_version='1.0.0',
            component=component,
            component_descriptor_lookup=lambda x: component_descriptor,
            version_lookup=lambda x: [],
            oci_client=None,
            base_path=tmpdir
        )
        assert os.path.exists(archive_path), 'archive not created'

        resources = [r.name for r in component.resources]
        assert 'release-notes-archive' in resources, 'resource missing!'

        assert 'Fix dummy bug' in release_notes_md
        assert 'Fix dummy bug' in full_release_notes_md
        assert 'Subcomponent Fix' in full_release_notes_md
