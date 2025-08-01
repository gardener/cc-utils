import os

import release_notes.tarutil as rnt
import release_notes.model as rnm


def persist_release_notes(
    notes: list[rnm.ReleaseNotesDoc],
    base_path: str = '.',
    archive_name: str = 'release-notes-archive.tar',
) -> str:
    archive_path = os.path.join(base_path, archive_name)

    with open(archive_path, 'wb') as f:
        for chunk in rnt.release_notes_docs_into_tarstream(notes):
            f.write(chunk)

    return archive_path


def extract_release_notes(
    archive_path: str,
    repo_dir: str,
    rel_path: str = '.ocm/release-notes'
):
    with open(archive_path, 'rb') as f:
        tarstream = iter(lambda: f.read(8192), b'')
        rnt.tarstream_into_release_notes_files(
            tarstream=tarstream,
            repo_dir=repo_dir,
            rel_path=rel_path
        )
