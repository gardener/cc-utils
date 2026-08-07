#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
'''
Post-hoc Go module CBOM scan for OCI images in an OCM component tree.

For each OCI image resource that does not yet have a go-module-inferred CBOM referrer,
fetch its existing CycloneDX SBOM from the OCI referrers API, infer crypto usage from
Go module dependencies, and push the result as an additional CBOM referrer manifest
annotated with `gardener.cloud/cbom/analysis-method: go-binary-inference`.

Images are skipped when:
  - they already have a go-module-inferred CBOM referrer (idempotent re-runs are cheap)
  - no CycloneDX SBOM referrer exists (scan.py has not yet run for this image)
  - the SBOM contains no Go module dependencies

No image layer downloads are performed — the SBOM blobs are small JSON documents already
stored as OCI referrers by the main pipeline.

This script fills the gap for images that were scanned before the pipeline-integrated
inference (in scan.py / sbom/inject.py) was deployed.  It becomes progressively less
useful as the pipeline integration runs on new images, and can be retired once all
images in the target landscape have been re-scanned.
'''
import argparse
import os
import sys

_cc_utils_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _cc_utils_root)

import cnudie.retrieve
import hashlib
import oci.auth
import oci.client
import oci.model as om
import ocm
import ocm.iter as ocm_iter
import sbom.cbom as scbom
import sbom.gobinary as sgob


def _resolve_single_arch_ref(
    image_ref: str | om.OciImageReference,
    oci_client: oci.client.Client,
) -> str | None:
    try:
        ref = om.OciImageReference.to_image_ref(image_ref)
        manifest = oci_client.manifest(ref, accept=om.MimeTypes.prefer_multiarch)
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
            return f'{ref.ref_without_tag}@{entry.digest}'
        digest = f'sha256:{hashlib.sha256(oci_client.manifest_raw(ref).content).hexdigest()}'
        return f'{ref.ref_without_tag}@{digest}'
    except Exception as exc:
        print(f'warning: cannot resolve {image_ref}: {exc}', file=sys.stderr)
        return None


def _write_summary(summary: str, append: bool = True) -> None:
    path = os.environ.get('GITHUB_STEP_SUMMARY')
    if not path:
        return
    with open(path, 'a' if append else 'w') as f:
        f.write(summary + '\n')


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            'Post-hoc Go module CBOM inference for OCI images lacking an inferred CBOM referrer.'
        ),
    )
    parser.add_argument('--ocm-component', required=True, metavar='NAME:VERSION')
    parser.add_argument(
        '--ocm-repository',
        required=True,
        action='append',
        dest='ocm_repositories',
        metavar='URL',
    )
    args = parser.parse_args()

    if ':' not in args.ocm_component:
        print(
            f'error: --ocm-component must be name:version, got: {args.ocm_component!r}',
            file=sys.stderr,
        )
        sys.exit(1)

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
    print(f'discovered {len(all_nodes)} resources total', file=sys.stderr)

    scanned = skipped_cached = skipped_no_sbom = skipped_no_go = failed = 0

    for node in all_nodes:
        resource = node.resource
        access = resource.access
        if not isinstance(access, ocm.OciAccess):
            continue
        if resource.type is not ocm.ArtefactType.OCI_IMAGE:
            continue

        image_ref = om.OciImageReference.to_image_ref(access.imageReference)
        digest_ref = _resolve_single_arch_ref(image_ref, oci_client)
        if digest_ref is None:
            print(
                f'warning: skipping {resource.name!r} — cannot resolve single-arch ref',
                file=sys.stderr,
            )
            continue

        # Skip cheaply if an inferred CBOM referrer already exists.
        if sgob.has_inferred_cbom(digest_ref, oci_client):
            skipped_cached += 1
            print(f'skip (cached): {resource.name}', file=sys.stderr)
            continue

        # Fetch the existing CycloneDX SBOM — no layer download needed.
        cdx_bytes = sgob.fetch_cdx_sbom(digest_ref, oci_client)
        if cdx_bytes is None:
            skipped_no_sbom += 1
            print(f'skip (no CycloneDX SBOM): {resource.name}', file=sys.stderr)
            continue

        inference = sgob.infer_from_cdx(cdx_bytes)
        if inference is None:
            skipped_no_go += 1
            print(f'skip (no Go modules): {resource.name}', file=sys.stderr)
            continue

        print(
            f'scanning: {resource.name}  '
            f'modules={inference["module_count"]}  ref={digest_ref}',
            file=sys.stderr,
        )
        inferred_cbom_bytes = sgob.build_inferred_cbom(
            image_ref=digest_ref,
            inference=inference,
        )
        try:
            scbom.push_cbom_referrer(
                cbom_bytes=inferred_cbom_bytes,
                image_reference=digest_ref,
                oci_client=oci_client,
                tool_version=sgob.TOOL_NAME,
                extra_annotations={
                    sgob.ANALYSIS_METHOD_ANNOTATION: sgob.ANALYSIS_METHOD_VALUE,
                },
            )
        except Exception as exc:
            print(f'error: {resource.name}: push failed: {exc}', file=sys.stderr)
            failed += 1
            continue

        print(
            f'ok: {resource.name}  '
            f'algorithms={len(inference["algorithms"])}  '
            f'protocols={len(inference["protocols"])}',
            file=sys.stderr,
        )
        scanned += 1

    summary_lines = [
        f'## Go module CBOM inference — {name}:{version}',
        '',
        '| | count |',
        '|---|---|',
        f'| discovered resources | {len(all_nodes)} |',
        f'| skipped (inferred CBOM exists) | {skipped_cached} |',
        f'| skipped (no CycloneDX SBOM) | {skipped_no_sbom} |',
        f'| skipped (no Go modules) | {skipped_no_go} |',
        f'| inferred + pushed | {scanned} |',
        f'| failed | {failed} |',
    ]
    summary = '\n'.join(summary_lines)
    print(f'\n{summary}', file=sys.stderr)
    _write_summary(summary)

    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
