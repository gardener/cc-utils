import concurrent.futures
import logging
import sys

import gci.componentmodel as cm

import ccc.oci
import cnudie.iter
import cnudie.purge
import cnudie.retrieve
import cnudie.util
import cnudie.validate
import ctx
import version

logger = logging.getLogger(__name__)


def _ocm_lookup(ocm_repo: str=None):
    if ocm_repo:
        return cnudie.retrieve.create_default_component_descriptor_lookup(
            ocm_repository_lookup=cnudie.retrieve.ocm_repository_lookup(ocm_repo),
        )
    else:
        return ctx.cfg.ctx.ocm_lookup


def retrieve(
    name: str,
    version: str=None,
    ocm_repo: str=None,
    out: str=None
):
    if not version:
        name, version = name.rsplit(':', 1)

    ocm_lookup = _ocm_lookup(ocm_repo=ocm_repo)

    component_descriptor = ocm_lookup(
        cm.ComponentIdentity(
            name=name,
            version=version,
        ),
    )

    if not component_descriptor:
        print(f'Error: did not find {name}:{version}')
        exit(1)

    if out:
        outfh = open(out, 'w')
    else:
        outfh = sys.stdout

    component_descriptor.to_fobj(fileobj=outfh)
    outfh.flush()
    outfh.close()


def validate(
    name: str,
    version: str,
    ocm_repo: str=None,
    out: str=None
):
    ocm_lookup = _ocm_lookup(ocm_repo=ocm_repo)

    logger.info('retrieving component-descriptor..')
    component_descriptor = ocm_lookup(
        cm.ComponentIdentity(
            name=name,
            version=version,
        ),
    )
    component = component_descriptor.component
    logger.info('validating component-descriptor..')

    violations = tuple(
        cnudie.validate.iter_violations(
            nodes=cnudie.iter.iter(
                component=component,
                recursion_depth=0,
            ),
        )
    )

    if not violations:
        logger.info('component-descriptor looks good')
        return

    logger.warning('component-descriptor yielded validation-errors (see below)')
    print()

    for violation in violations:
        print(violation.as_error_message)


def ls(
    name: str,
    greatest: bool=False,
    final: bool=False,
    ocm_repo: str=None,
):
    if ocm_repo:
        ocm_repo_lookup = cnudie.retrieve.ocm_repository_lookup(ocm_repo)
    else:
        ocm_repo_lookup = ctx.cfg.ctx.ocm_repository_lookup

    version_lookup = cnudie.retrieve.version_lookup(ocm_repository_lookup=ocm_repo_lookup)

    ocm_repo = next(ocm_repo_lookup(name))

    if greatest:
        print(version.greatest_version(
            versions=version_lookup(name),
        ))
        return

    versions = version_lookup(name)

    for v in versions:
        if final:
            parsed_version = version.parse_to_semver(v)
            if parsed_version.prerelease:
                continue
        print(v)


def purge_old(
    name: str,
    final: bool=False,
    ocm_repo: str=None,
    keep: int=256,
    threads: int=32,
):
    if ocm_repo:
        ocm_repo_lookup = cnudie.retrieve.ocm_repository_lookup(ocm_repo)
    else:
        ocm_repo_lookup = ctx.cfg.ctx.ocm_repository_lookup

    ocm_repo = next(ocm_repo_lookup(name))

    version_lookup = cnudie.retrieve.version_lookup(ocm_repository_lookup=ocm_repo_lookup)

    versions = version_lookup(name)

    if not final:
        versions = [
            v for v in versions
            if not version.parse_to_semver(v).prerelease
        ]

    versions = version.smallest_versions(
        versions=versions,
        keep=keep,
    )

    print(f'will rm {len(versions)} version(s) using {threads=}')

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=threads)
    oci_client = ccc.oci.oci_client(
        http_connection_pool_size=threads,
    )

    def purge_component_descriptor(ref: str):
        oci_client.delete_manifest(
            image_reference=ref,
            purge=True,
        )
        print(f'purged: {ref}')

    def iter_oci_refs_to_rm():
        for v in versions:
            ref = f'{ocm_repo}/component-descriptors/{name}:{v}'
            yield pool.submit(
                purge_component_descriptor,
                ref=ref,
            )

    for ref in concurrent.futures.as_completed(iter_oci_refs_to_rm()):
        pass


def purge(
    name: str,
    recursive: bool=False,
    version: str=None,
    ocm_repo: str=None,
):
    if not version:
        name, version = name.rsplit(':', 1)

    ocm_lookup = _ocm_lookup(ocm_repo=ocm_repo)

    lookup = cnudie.retrieve.oci_component_descriptor_lookup()

    component_descriptor = ocm_lookup(
        cm.ComponentIdentity(
            name=name,
            version=version,
        ),
    )

    oci_client = ccc.oci.oci_client()

    cnudie.purge.remove_component_descriptor_and_referenced_artefacts(
        component=component_descriptor.component,
        oci_client=oci_client,
        lookup=lookup,
        recursive=recursive,
    )
