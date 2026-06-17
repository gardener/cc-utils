#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
import argparse
import os
import sys

import cnudie.retrieve
import oci.auth
import oci.client
import ocm
import ocm.iter as ocm_iter
import sbom.iter as si
import sbom.oci as soci


def _oci_client(parsed) -> oci.client.Client:
    docker_cfg = parsed.docker_cfg
    if docker_cfg and not os.path.exists(docker_cfg):
        print(f'Error: not an existing file: {docker_cfg=}')
        sys.exit(1)

    if not docker_cfg:
        for candidate in (
            os.path.expandvars('$HOME/.docker/config.json'),
            '/docker-cfg.json',
        ):
            if os.path.exists(candidate):
                docker_cfg = candidate
                break

    return oci.client.Client(
        credentials_lookup=oci.auth.docker_credentials_lookup(
            docker_cfg=docker_cfg,
            absent_ok=True,
        ),
    )


def _mangle(name: str) -> str:
    return name.replace('/', '_')


def _sbom_format_id(mapping: si.SbomMapping) -> str | None:
    '''Derive the sbom format identifier (e.g. "spdx-2.3") from a SbomMapping.'''
    if mapping.source is si.SbomSource.OCM:
        return (mapping.sbom.extraIdentity or {}).get('sbom-format')
    # OCI_REFERRER: reverse-map artifact_type -> format_id via SBOM_FORMATS
    for fmt_id, media_type in soci.SBOM_FORMATS:
        if mapping.sbom.artifact_type == media_type:
            return fmt_id
    return None


def _sbom_filename(
    component: ocm.Component,
    resource: ocm.Resource,
    format_id: str,
) -> str:
    '''
    Build the output filename for one SBOM document.

    Convention:
      {component-name:version}_{resource-name:version}[-extra-val…].sbom.{format-id}

    Extra-identity values are taken from the payload resource (not the SBOM artefact),
    excluding 'sbom-format' and 'version', sorted by key for stability, joined with '-'.
    Name components have '/' replaced with '_'.
    '''
    extra_vals = [
        v for k, v in sorted((resource.extraIdentity or {}).items())
        if k not in ('sbom-format', 'version')
    ]
    extra_suffix = ('-' + '-'.join(extra_vals)) if extra_vals else ''
    return (
        f'{_mangle(component.name)}:{component.version}'
        f'_{_mangle(resource.name)}:{resource.version}'
        f'{extra_suffix}'
        f'.sbom.{format_id}'
    )


def _fetch_sboms(parsed):
    oci_client = _oci_client(parsed)

    component_ref = parsed.component
    try:
        name, version = component_ref.rsplit(':', 1)
    except ValueError:
        print(f'Error: component must be in name:version format, got {component_ref!r}')
        sys.exit(1)

    outdir = parsed.outdir
    if not os.path.isdir(outdir):
        print(f'Error: not an existing directory: {outdir!r}')
        sys.exit(1)

    format_prefixes: list[str] = parsed.sbom_formats

    ocm_repo_lookup = cnudie.retrieve.ocm_repository_lookup(parsed.ocm_repository)
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

    written = 0
    missing = 0

    for node in ocm_iter.iter_resources(component=root_component, lookup=lookup):
        component = node.component
        resource = node.resource

        # for each resource, collect one mapping per format-prefix (lazy)
        found: dict[str, si.SbomMapping] = {}  # prefix -> mapping

        for mapping in si.iter_sboms_for_resource(
            resource=resource,
            component=component,
            oci_client=oci_client,
        ):
            fmt_id = _sbom_format_id(mapping)
            if fmt_id is None:
                continue
            for prefix in format_prefixes:
                if prefix not in found and fmt_id.startswith(prefix):
                    found[prefix] = mapping
            if len(found) == len(format_prefixes):
                break  # all formats satisfied — skip remaining (OCI referrer calls avoided)

        for prefix, mapping in found.items():
            fmt_id = _sbom_format_id(mapping)
            filename = _sbom_filename(component, resource, fmt_id)
            outpath = os.path.join(outdir, filename)
            try:
                doc_bytes = si.fetch_sbom_document(mapping, oci_client)
            except Exception as e:
                print(f'warning: failed to fetch SBOM for {resource.name!r} ({fmt_id}): {e}')
                continue
            with open(outpath, 'wb') as f:
                f.write(doc_bytes)
            print(outpath)
            written += 1

        for prefix in format_prefixes:
            if prefix not in found:
                print(
                    f'warning: no {prefix!r} SBOM found for '
                    f'{component.name}:{component.version} '
                    f'/ {resource.name}:{resource.version}',
                    file=sys.stderr,
                )
                missing += 1

    if missing:
        print(f'{written} SBOM(s) written, {missing} missing.', file=sys.stderr)
        sys.exit(1)
    print(f'{written} SBOM(s) written.', file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description='Fetch SBOM documents for all resources in an OCM component tree.',
    )
    parser.add_argument(
        '--docker-cfg',
        default=None,
        help='path to docker config.json for OCI registry auth (default: ~/.docker/config.json)',
    )
    parser.add_argument(
        '--ocm-repository',
        required=True,
        help='OCM repository base URL (e.g. europe-docker.pkg.dev/gardener-project/releases)',
    )
    parser.add_argument(
        'component',
        help='root component in name:version format',
    )
    parser.add_argument(
        '--outdir',
        default='.',
        help='directory to write SBOM files into (default: current directory)',
    )
    parser.add_argument(
        '--sbom-formats',
        nargs='+',
        default=['spdx', 'cyclonedx'],
        metavar='FORMAT',
        help=(
            'SBOM format prefixes to retrieve per resource. '
            'Use exact version (e.g. spdx-2.3) or prefix (e.g. spdx) to match any version. '
            'Default: spdx cyclonedx'
        ),
    )

    parsed = parser.parse_args()
    _fetch_sboms(parsed)


if __name__ == '__main__':
    main()
