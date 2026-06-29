#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
'''
Scan OCI image resources in an OCM component tree that are missing SBOM referrers,
run syft + cbomkit-theia, and push the results as OCI referrer manifests.

Intended to run before upload.py so that the uploaded SBOMs include freshly scanned
documents. Referrers pushed here are discovered by iter_sboms_for_resource (phase 2)
on the next run, avoiding redundant scans.

OCM component descriptors and OCI image manifests are not modified.
'''
import argparse
import hashlib
import os
import sys
import tempfile

# ensure the cc-utils tree that contains this action is importable, so that
# local edits to sbom/ take effect even when an older gardener-gha-libs is
# already installed system-wide (the action dir is two levels below the root)
_cc_utils_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _cc_utils_root)


import cnudie.retrieve
import oci.auth
import oci.client
import oci.model as om
import ocm
import ocm.iter as ocm_iter
import sbom.inject as sinject


def _resolve_single_arch_ref(
    image_ref: str | om.OciImageReference,
    oci_client: oci.client.Client,
) -> str | None:
    '''
    Resolve `image_ref` to a digest-addressed single-arch image ref (linux/amd64 preferred).

    Handles manifest lists: fetches with prefer_multiarch Accept, then resolves to the
    linux/amd64 platform entry (falling back to the first entry if amd64 is absent).

    Returns the resolved digest ref string, or None on error.
    '''
    try:
        image_ref = om.OciImageReference.to_image_ref(image_ref)
        repo = image_ref.ref_without_tag
        manifest = oci_client.manifest(image_ref, accept=om.MimeTypes.prefer_multiarch)
        if isinstance(manifest, om.OciImageManifestList):
            entries = [
                e for e in manifest.manifests
                if e.platform and e.platform.os == 'linux'
                and e.platform.architecture == 'amd64'
            ]
            entry = entries[0] if entries else (
                manifest.manifests[0] if manifest.manifests else None
            )
            if entry is None:
                return None
            return f'{repo}@{entry.digest}'
        # single-arch: compute digest from manifest bytes
        manifest_bytes = oci_client.manifest_raw(image_ref).content
        digest = f'sha256:{hashlib.sha256(manifest_bytes).hexdigest()}'
        return f'{repo}@{digest}'
    except Exception as e:
        print(f'warning: cannot resolve single-arch ref for {image_ref}: {e}', file=sys.stderr)
        return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Scan missing SBOMs for all OCI image resources in an OCM component tree.',
    )
    parser.add_argument(
        '--ocm-component',
        required=True,
        metavar='NAME:VERSION',
    )
    parser.add_argument(
        '--ocm-repository',
        required=True,
        action='append',
        dest='ocm_repositories',
        metavar='URL',
    )
    args = parser.parse_args()

    if ':' not in args.ocm_component:
        print(f'error: --ocm-component must be name:version, got: {args.ocm_component!r}',
              file=sys.stderr)
        sys.exit(1)

    sinject.check_syft()
    sinject.check_cbomkit_theia()

    name, version = args.ocm_component.rsplit(':', 1)
    ocm_repositories = [r for r in args.ocm_repositories if r.strip()]

    oci_client = oci.client.Client(
        credentials_lookup=oci.auth.docker_credentials_lookup(absent_ok=True),
    )

    ocm_repo_lookup = cnudie.retrieve.ocm_repository_lookup(*ocm_repositories)
    lookup = cnudie.retrieve.composite_component_descriptor_lookup(
        lookups=(
            cnudie.retrieve.in_memory_cache_component_descriptor_lookup(
                ocm_repository_lookup=ocm_repo_lookup,
            ),
            cnudie.retrieve.oci_component_descriptor_lookup(
                ocm_repository_lookup=ocm_repo_lookup,
                oci_client=oci_client,
            ),
        ),
        ocm_repository_lookup=ocm_repo_lookup,
    )

    root_component = lookup(
        ocm.ComponentIdentity(name=name, version=version),
    ).component

    all_nodes = list(ocm_iter.iter_resources(component=root_component, lookup=lookup))
    print(f'discovered {len(all_nodes)} resources', file=sys.stderr)

    # collect OCI image resources that have no existing referrers
    missing = []
    for node in all_nodes:
        resource = node.resource
        access = resource.access
        if not isinstance(access, ocm.OciAccess):
            continue
        image_ref = om.OciImageReference.to_image_ref(access.imageReference)
        # resolve manifest list → linux/amd64 single-arch digest ref
        digest_ref = _resolve_single_arch_ref(image_ref, oci_client)
        if digest_ref is None:
            print(f'warning: cannot resolve single-arch ref for {resource.name!r}', file=sys.stderr)
            continue

        existing = sinject.lookup_sbom_referrers(
            image_ref=digest_ref,
            oci_client=oci_client,
        )
        if existing is not None:
            print(f'skip (referrers present): {resource.name}', file=sys.stderr)
            continue
        missing.append((resource.name, digest_ref))

    if not missing:
        print('all resources have existing SBOM referrers — nothing to scan', file=sys.stderr)
        return

    print(f'{len(missing)} resource(s) to scan', file=sys.stderr)

    with tempfile.TemporaryDirectory() as tmpdir:
        results = sinject.run_injections_resource_aware(
            items=missing,
            oci_client=oci_client,
            tmpdir=tmpdir,
        )

    scanned = sum(1 for r in results if r[-1] == 'scanned')
    failed  = sum(1 for r in results if r[-1] == 'failed')
    print(f'scanned: {scanned}, failed: {failed}', file=sys.stderr)
    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
