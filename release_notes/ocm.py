'''
another opinionated / gardener-specific module for managing release-notes in
OCM-Component-Descriptors
'''

import collections.abc
import logging
import os
import tarfile
import zlib

import cnudie.retrieve
import oci.client
import ocm
import ocm.gardener
import release_notes.model as rnm
import release_notes.tarutil as rnt
import version as version_mod

logger = logging.getLogger(__name__)


'''
Initially, the release-notes were attached as a plain markdown text document as local blob to the
OCM component descriptor. Those artefacts have the name `release_notes_resource_name_old`
(-> `release-notes`). After a refactoring, the release-notes are expected to be added in a
machine-readable format to the component descriptor (based on `release_notes.model.ReleaseNotesDoc`).
Those "new" artefacts have the name `release_notes_resource_name` (-> 'release-notes-archive'), since
they contain an archive of all recursively retrieved release-notes.
Once there are no component upgrades for components which have been release prior to the refactoring,
the support for the "old" release notes artefact can be dropped eventually.
'''
release_notes_resource_name = 'release-notes-archive'
release_notes_resource_name_old = 'release-notes'


def iter_parsed_release_notes(
    component: ocm.Component,
    resource: ocm.Resource,
    oci_client: oci.client.Client,
) -> collections.abc.Iterable[rnm.ReleaseNotesDoc]:
    if resource.access.type is not ocm.AccessType.LOCAL_BLOB:
        raise ValueError(f'do not know how to handle {resource.access.type=} ({component=})')

    access: ocm.LocalBlobAccess = resource.access

    image_reference = component.current_ocm_repo.component_oci_ref(component)
    digest = access.globalAccess.digest if access.globalAccess else access.localReference

    if resource.name == release_notes_resource_name_old:
        release_notes_blob = oci_client.blob(
            image_reference=image_reference,
            digest=digest,
        )

        if access.mediaType.endswith('/gzip'):
            release_notes_bytes = zlib.decompress(release_notes_blob.content, wbits=31)
        else:
            release_notes_bytes = release_notes_blob.content

        yield rnm.ReleaseNotesDoc(
            ocm=rnm.ReleaseNotesOcmRef(
                component_name=component.name,
                component_version=component.version,
            ),
            release_notes=[
                rnm.ReleaseNoteEntry(
                    type=rnm.ReleaseNotesType.PRERENDERED,
                    contents=release_notes_bytes.decode('utf-8'),
                    mimetype=access.mediaType.split('.')[0],
                ),
            ],
        )
        return

    release_notes_blob_tarstream = oci_client.blob(
        image_reference=image_reference,
        digest=digest,
    ).iter_content(chunk_size=tarfile.RECORDSIZE)

    return rnt.tarstream_into_release_notes_docs(release_notes_blob_tarstream)


def find_release_notes_resource(
    component: ocm.Component,
    resource_name: str=release_notes_resource_name,
    absent_ok: bool=True,
) -> ocm.Resource | None:
    for resource in component.resources:
        if resource.name == resource_name:
            return resource

    if absent_ok:
        return None

    raise ValueError(f'{component=} has no resource named `{resource_name}`')


def release_notes_for_vector(
    upgrade_vector: ocm.gardener.UpgradeVector,
    component_descriptor_lookup: ocm.ComponentDescriptorLookup,
    version_lookup: ocm.VersionLookup,
    oci_client: oci.client.Client,
    version_filter: collections.abc.Callable[[str], bool]=lambda _: True,
) -> collections.abc.Iterable[rnm.ReleaseNotesDoc]:
    '''
    Yields release-notes documents (pairs of OCM component-ids together with their release-notes)
    for all (sub-)components within the provided `upgrade_vector`. If a component-id does not have
    any release-notes, it may just be omitted.

    If a component contains a "new" release-notes blob, it is retrieved and yielded and stopped
    afterwards because it already contains the (modified) release-notes of all sub-components as
    well.
    If a component still only contains an "old" release-notes blob (or none), it is parsed and
    yielded (if it exists) but this function will be invoked again for all direct sub-components.
    '''
    versions = [
        version
        for version in version_lookup(upgrade_vector.component_name)
        if version_filter(version)
    ]

    versions_in_range = list(version_mod.iter_upgrade_path(
        whence=upgrade_vector.whence_version,
        whither=upgrade_vector.whither_version,
        versions=versions,
    ))

    for idx, version in enumerate(versions_in_range):
        component_id = ocm.ComponentIdentity(
            name=upgrade_vector.component_name,
            version=version,
        )

        component = component_descriptor_lookup(component_id).component

        if release_notes_resource := find_release_notes_resource(
            component=component,
        ):
            logger.info(f'found release-notes resource for {component_id=}')
            yield from iter_parsed_release_notes(
                component=component,
                resource=release_notes_resource,
                oci_client=oci_client,
            )
            continue

        if release_notes_resource := find_release_notes_resource(
            component=component,
            resource_name=release_notes_resource_name_old,
        ):
            logger.info(f'found "old" release-notes resource for {component_id=}')
            yield from iter_parsed_release_notes(
                component=component,
                resource=release_notes_resource,
                oci_client=oci_client,
            )

        # get the predecessor version of the upgrade-path to build "whence" component for diff
        if idx > 0:
            predecessor_version = versions_in_range[idx - 1]
        else:
            # the initial "whence" version is excluded in the upgrade-path
            predecessor_version = upgrade_vector.whence.version

        whence_component = component_descriptor_lookup(ocm.ComponentIdentity(
            name=upgrade_vector.component_name,
            version=predecessor_version,
        )).component

        yield from release_notes_for_subcomponents(
            whence_component=whence_component,
            whither_component=component,
            component_descriptor_lookup=component_descriptor_lookup,
            version_lookup=version_lookup,
            oci_client=oci_client,
            version_filter=version_filter,
        )


def release_notes_for_subcomponents(
    whence_component: ocm.Component,
    whither_component: ocm.Component,
    component_descriptor_lookup: ocm.ComponentDescriptorLookup,
    version_lookup: ocm.VersionLookup,
    oci_client: oci.client.Client,
    version_filter: collections.abc.Callable[[str], bool]=lambda _: True,
) -> collections.abc.Iterable[rnm.ReleaseNotesDoc]:
    component_diff = cnudie.retrieve.component_diff(
        left_component=whence_component,
        right_component=whither_component,
        component_descriptor_lookup=component_descriptor_lookup,
        recursion_depth=1, # only calculate diff of direct sub-components
    )

    for whence, whither in component_diff.cpairs_version_changed:
        if whither.identity() == whither_component.identity():
            # we are only interested in the release-notes for sub-components here, not the root
            continue

        upgrade_vector = ocm.gardener.UpgradeVector(
            whence=whence.identity(),
            whither=whither.identity(),
        )

        yield from release_notes_for_vector(
            upgrade_vector=upgrade_vector,
            component_descriptor_lookup=component_descriptor_lookup,
            version_lookup=version_lookup,
            oci_client=oci_client,
            version_filter=version_filter,
        )


def group_release_notes_docs(
    release_notes_docs: collections.abc.Iterable[rnm.ReleaseNotesDoc],
) -> list[rnm.ReleaseNotesDoc]:
    docs_by_component_id: dict[ocm.ComponentIdentity, rnm.ReleaseNotesDoc] = {}

    for doc in release_notes_docs:
        if doc.component_id in docs_by_component_id:
            docs_by_component_id[doc.component_id].release_notes.extend(doc.release_notes)
        else:
            docs_by_component_id[doc.component_id] = doc

    return list(docs_by_component_id.values())


def release_notes_docs_as_markdown(
    release_notes_docs: collections.abc.Sequence[rnm.ReleaseNotesDoc],
    prepend_title: bool=True,
) -> str | None:
    if not release_notes_docs:
        return None

    if prepend_title:
        release_notes_md = '**Release Notes**:\n\n'
    else:
        release_notes_md = ''

    return release_notes_md + '\n\n'.join(
        markdown
        for release_notes_doc in release_notes_docs
        if (markdown := release_notes_doc.as_markdown())
    )


def purge_release_notes_dir(
    repo_dir: str,
    rel_path: str='.ocm/release-notes',
    absent_ok: bool=True,
):
    repo_dir = os.path.abspath(repo_dir)
    dir_path = os.path.abspath(os.path.join(repo_dir, rel_path))

    if not os.path.commonpath([repo_dir, dir_path]) == repo_dir:
        raise ValueError(f'{rel_path=} points outside of {repo_dir=}')

    if not os.path.isdir(dir_path):
        if not absent_ok:
            raise RuntimeError(f'{dir_path=} is not a directory')

        logger.info(f'{dir_path=} is not a directory, skipping purging of release-notes documents')
        return

    logger.info(
        f'going to purge release-notes docs with {rnm.RELEASE_NOTES_DOC_SUFFIX=} in {dir_path=}'
    )
    for cur_dir_path, dirnames, fnames in os.walk(dir_path):
        for dirname in dirnames:
            if dirname.endswith(rnm.RELEASE_NOTES_DOC_SUFFIX):
                raise ValueError(f'found {dirname=} with matching {rnm.RELEASE_NOTES_DOC_SUFFIX=}')

        for fname in fnames:
            file_path = os.path.join(cur_dir_path, fname)

            if not file_path.endswith(rnm.RELEASE_NOTES_DOC_SUFFIX):
                logger.info(f'skipping deletion of {file_path=} (suffix does not match)')
                continue

            os.remove(file_path)
