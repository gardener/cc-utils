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
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

_cc_utils_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _cc_utils_root)

import cnudie.retrieve
import oci.auth
import oci.client
import oci.model as om
import ocm
import ocm.iter as ocm_iter
import sbom.cbom as scbom
import sbom.gobinary as sgob


_gcloud_token_cache = {'token': None, 'expires_at': 0.0}


def _gcloud_token():
    if _gcloud_token_cache['token'] and time.time() < _gcloud_token_cache['expires_at']:
        return _gcloud_token_cache['token']
    adc_path = os.path.join(
        os.environ.get('HOME', ''),
        '.config', 'gcloud', 'application_default_credentials.json',
    )
    try:
        with open(adc_path) as f:
            creds = json.load(f)
        if creds.get('type') == 'authorized_user':
            data = urllib.parse.urlencode({
                'client_id':     creds['client_id'],
                'client_secret': creds['client_secret'],
                'refresh_token': creds['refresh_token'],
                'grant_type':    'refresh_token',
            }).encode()
            req = urllib.request.Request(
                'https://oauth2.googleapis.com/token', data=data, method='POST',
            )
            with urllib.request.urlopen(req) as resp:  # nosec B310
                result = json.loads(resp.read())
            _gcloud_token_cache['token']      = result['access_token']
            _gcloud_token_cache['expires_at'] = time.time() + result['expires_in'] - 30
            return _gcloud_token_cache['token']
    except Exception:  # nosec B110
        pass
    try:
        result = subprocess.run(
            ['gcloud', 'auth', 'print-access-token'],  # nosec B607
            capture_output=True,
            text=True,
            check=True,
        )
        token = result.stdout.strip()
        _gcloud_token_cache['token']      = token
        _gcloud_token_cache['expires_at'] = time.time() + 3300
        return token
    except Exception:
        return None


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
    parser.add_argument(
        '--force',
        action='store_true',
        help='Re-push even when an inferred CBOM referrer already exists.',
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

    docker_lookup = oci.auth.docker_credentials_lookup(absent_ok=True)

    def credentials_lookup(image_reference, privileges=None, absent_ok=True):
        ref = om.OciImageReference.to_image_ref(image_reference)
        if ref.registry_type is om.OciRegistryType.GAR:
            token = _gcloud_token()
            if token:
                return oci.auth.OciBasicAuthCredentials(
                    username='oauth2accesstoken',
                    password=token,
                )
        return docker_lookup(image_reference, privileges, absent_ok)

    oci_client = oci.client.Client(
        credentials_lookup=credentials_lookup,
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
    oci_images = [
        n for n in all_nodes
        if isinstance(n.resource.access, ocm.OciAccess)
        and n.resource.type is ocm.ArtefactType.OCI_IMAGE
    ]
    total = len(oci_images)
    print(f'discovered {len(all_nodes)} resources total ({total} OCI images)', file=sys.stderr)

    scanned = skipped_cached = skipped_no_sbom = skipped_no_go = failed = 0
    idx = 0
    t_start = time.monotonic()

    for node in oci_images:
        idx += 1
        resource = node.resource
        access = resource.access
        t0 = time.monotonic()
        prefix = f'[{idx}/{total}]'

        image_ref = om.OciImageReference.to_image_ref(access.imageReference)
        digest_ref = _resolve_single_arch_ref(image_ref, oci_client)
        if digest_ref is None:
            print(
                f'{prefix} warning: skipping {resource.name!r} — cannot resolve single-arch ref',
                file=sys.stderr,
            )
            continue

        # Skip cheaply if an inferred CBOM referrer already exists.
        if not args.force and sgob.has_inferred_cbom(digest_ref, oci_client):
            skipped_cached += 1
            elapsed = time.monotonic() - t0
            print(f'{prefix} skip (cached): {resource.name}  ({elapsed:.1f}s)', file=sys.stderr)
            continue

        # Fetch the existing CycloneDX SBOM — no layer download needed.
        cdx_bytes = sgob.fetch_cdx_sbom(digest_ref, oci_client)
        if cdx_bytes is None:
            skipped_no_sbom += 1
            elapsed = time.monotonic() - t0
            print(
                f'{prefix} skip (no CycloneDX SBOM): {resource.name}  ({elapsed:.1f}s)',
                file=sys.stderr,
            )
            continue

        inference = sgob.infer_from_cdx(cdx_bytes)
        if inference is None:
            skipped_no_go += 1
            elapsed = time.monotonic() - t0
            print(
                f'{prefix} skip (no Go modules): {resource.name}  ({elapsed:.1f}s)',
                file=sys.stderr,
            )
            continue

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
            elapsed = time.monotonic() - t0
            print(
                f'{prefix} error: {resource.name}: push failed: {exc}  ({elapsed:.1f}s)',
                file=sys.stderr,
            )
            failed += 1
            continue

        elapsed = time.monotonic() - t0
        done = skipped_cached + skipped_no_sbom + skipped_no_go + scanned + failed + 1
        elapsed_total = time.monotonic() - t_start
        eta = (elapsed_total / done) * (total - done) if done < total else 0
        print(
            f'{prefix} ok: {resource.name}  '
            f'modules={inference["module_count"]}  '
            f'algorithms={len(inference["algorithms"])}  '
            f'protocols={len(inference["protocols"])}  '
            f'({elapsed:.1f}s, ETA {eta:.0f}s)',
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
