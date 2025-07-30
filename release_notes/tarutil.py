import collections.abc
import dataclasses
import enum
import io
import logging
import os
import tarfile

import dacite
import yaml

import ioutil
import ocm
import release_notes.model as rnm
import tarutil


logger = logging.getLogger(__name__)


def tar_filter(
    member: tarfile.TarInfo,
    *args,
    **kwargs,
) -> tarfile.TarInfo | None:
    if (
        not member.isfile()
        or ((member.islnk() or member.issym) and os.path.isabs(member.linkname))
    ):
        logger.warning(f'skipped tarfile {member.name=} because of applied filter')
        return None

    return member


def release_notes_docs_into_tarstream(
    release_notes_docs: collections.abc.Iterable[rnm.ReleaseNotesDoc],
) -> collections.abc.Iterable[bytes]:
    # use yaml block-style indicator for multiline strings
    yaml.add_representer(
        data_type=str,
        representer=lambda dumper, data: dumper.represent_scalar(
            tag='tag:yaml.org,2002:str',
            value=data,
            style='|' if data.count('\n') > 0 else None,
        ),
        Dumper=ocm.EnumValueYamlDumper,
    )

    def release_notes_doc_to_blob_descriptor(
        release_notes_doc: rnm.ReleaseNotesDoc,
    ) -> ioutil.BlobDescriptor:
        release_notes_doc_bytes = yaml.dump(
            data=dataclasses.asdict(release_notes_doc),
            Dumper=ocm.EnumValueYamlDumper,
            allow_unicode=True,
        ).encode('utf-8')

        return ioutil.BlobDescriptor(
            content=io.BytesIO(release_notes_doc_bytes),
            size=len(release_notes_doc_bytes),
            name=release_notes_doc.fname
        )

    return tarutil.concat_blobs_as_tarstream(
        blobs=(
            release_notes_doc_to_blob_descriptor(release_notes_doc)
            for release_notes_doc in release_notes_docs
        ),
    )


def tarstream_into_release_notes_docs(
    tarstream: collections.abc.Iterable[bytes],
) -> collections.abc.Iterable[rnm.ReleaseNotesDoc]:
    tar = tarfile.open(
        fileobj=tarutil.FilelikeProxy(tarstream),
        mode='r|*',
    )

    for member in tar:
        if not tar_filter(member):
            continue

        fileobj = tar.extractfile(member=member)

        release_notes_doc_raw = yaml.safe_load(fileobj)
        release_notes_doc = dacite.from_dict(
            data_class=rnm.ReleaseNotesDoc,
            data=release_notes_doc_raw,
            config=dacite.Config(
                cast=[enum.Enum],
            ),
        )

        yield release_notes_doc


def tarstream_into_release_notes_files(
    tarstream: collections.abc.Iterable[bytes],
    repo_dir: str,
    rel_path: str='.ocm/release-notes',
):
    release_notes_dir = os.path.join(repo_dir, rel_path)

    os.makedirs(
        name=release_notes_dir,
        exist_ok=True,
    )

    with tarfile.open(fileobj=tarutil.FilelikeProxy(tarstream), mode='r|*') as tar:
        tar.extractall( # nosec B202 files are already being filtered by `tar_filter`
            path=release_notes_dir,
            filter=tar_filter,
        )


def release_notes_docs_into_files(
    release_notes_docs: collections.abc.Iterable[rnm.ReleaseNotesDoc],
    repo_dir: str,
    rel_path: str='.ocm/release-notes',
):
    tarstream = release_notes_docs_into_tarstream(
        release_notes_docs=release_notes_docs,
    )

    tarstream_into_release_notes_files(
        tarstream=tarstream,
        repo_dir=repo_dir,
        rel_path=rel_path,
    )
