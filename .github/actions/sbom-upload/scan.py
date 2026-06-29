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
import sys
import tempfile

import cnudie.retrieve
import oci.auth
import oci.client
import oci.model as om
import ocm
import ocm.iter as ocm_iter
import sbom.inject as sinject


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
        digest_ref = sinject._resolve_single_arch_ref(image_ref, oci_client)
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
