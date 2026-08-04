#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
'''
Ad-hoc integration test for CTT SBOM injection — full pipeline.

Requires:
  - write access to europe-docker.pkg.dev/gardener-project/snapshots/ctt-test
  - read access to europe-docker.pkg.dev/gardener-project/releases
  - syft on PATH
  - docker credentials for both registries

Fetches the real component-descriptor for
  github.com/gardener/gardener-extension-provider-openstack:v1.55.3
from the /releases registry, runs the full CTT replication pipeline with
SbomInjectionProcessor targeting /snapshots/ctt-test/<run_id>, then asserts
that SBOM resources (spdx-2.3 + cyclonedx-1.6) were injected.
'''
import logging
import os
import sys
import tempfile

import yaml

import cnudie.retrieve
import ctt.process_dependencies as pdeps
import oci.auth as oa
import oci.client as oc
import ocm
import ocm.iter

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger('sbom-inject-inttest')

RELEASES_REGISTRY = 'europe-docker.pkg.dev/gardener-project/releases'
SNAPSHOTS_REGISTRY = 'europe-docker.pkg.dev/gardener-project/snapshots'
TGT_REGISTRY = f'{SNAPSHOTS_REGISTRY}/ctt-test'

COMPONENT_NAME = 'github.com/gardener/gardener-extension-provider-openstack'
COMPONENT_VERSION = 'v1.55.3'


def _oci_lookup(ocm_repo_url: str, oci_client: oc.Client):
    '''Build a lightweight OCI-only component descriptor lookup (no ctx/config system).'''
    return cnudie.retrieve.oci_component_descriptor_lookup(
        ocm_repository_lookup=cnudie.retrieve.ocm_repository_lookup(ocm_repo_url),
        oci_client=oci_client,
    )


def _processing_cfg(tgt: str) -> dict:
    return {
        'targets': {
            'default': {
                'type': 'RegistriesTarget',
                'kwargs': {
                    'registries': [tgt],
                    'ocm_repository': tgt,
                },
            },
        },
        'processors': {
            'sbom': {
                'type': 'SbomInjectionProcessor',
            },
        },
        'uploaders': {
            'prepend': {
                'type': 'PrependTargetUploader',
                'kwargs': {
                    'remove_prefixes': [RELEASES_REGISTRY, 'registry.k8s.io'],
                },
            },
        },
        'image_processing_cfg': [
            {
                'name': 'sbom-inject',
                'filter': [{'type': 'MatchAllFilter'}],
                'processor': 'sbom',
                'target': 'default',
                'upload': ['prepend'],
            },
        ],
    }


def run(run_id: str):
    oci_client = oc.Client(
        credentials_lookup=oa.docker_credentials_lookup(),
    )

    src_lookup = _oci_lookup(RELEASES_REGISTRY, oci_client)

    root_cd = src_lookup(ocm.ComponentIdentity(
        name=COMPONENT_NAME,
        version=COMPONENT_VERSION,
    ))
    logger.info(
        f'fetched root component: {root_cd.component.name}:{root_cd.component.version} '
        f'({len(root_cd.component.resources)} resources)'
    )

    tgt = f'{TGT_REGISTRY}/{run_id}'
    cfg = _processing_cfg(tgt)
    tgt_lookup = _oci_lookup(tgt, oci_client)

    with tempfile.TemporaryDirectory() as tmpdir:
        cfg_path = os.path.join(tmpdir, 'processing.cfg')
        with open(cfg_path, 'w') as f:
            yaml.safe_dump(cfg, f)

        os.environ['TMPDIR'] = tmpdir

        processing_cfg = pdeps.parse_processing_cfg(cfg_path)

        replication_plan_step = pdeps.create_replication_plan_step(
            processing_cfg=processing_cfg,
            root_component_descriptor=root_cd,
            src_component_descriptor_lookup=src_lookup,
            tgt_component_descriptor_lookup=tgt_lookup,
            ocm_repository=tgt,
            tgt_oci_registries=[tgt],
            oci_client=oci_client,
        )

        logger.info(replication_plan_step)

        nodes = list(pdeps.process_replication_plan_step(
            replication_plan_step=replication_plan_step,
            root_component_descriptor=root_cd,
            oci_client=oci_client,
            tgt_component_descriptor_lookup=tgt_lookup,
            skip_cd_validation=True,
        ))

    logger.info(f'process_replication_plan_step yielded {len(nodes)} node(s)')

    resource_nodes = [n for n in nodes if ocm.iter.Filter.resources(n)]
    all_resources = [n.resource for n in resource_nodes]

    sbom_resources = [
        r for r in all_resources
        if isinstance(r.extraIdentity, dict) and r.extraIdentity.get('sbom-format')
    ]
    oci_resources = [
        r for r in all_resources
        if r.access.type is ocm.AccessType.OCI_REGISTRY
        and not (isinstance(r.extraIdentity, dict) and r.extraIdentity.get('sbom-format'))
    ]

    logger.info(
        f'{len(oci_resources)} OCI image(s) replicated, '
        f'{len(sbom_resources)} SBOM resource(s) injected'
    )

    assert sbom_resources, (
        f'no SBOM resources injected; all resources: '
        f'{[(r.name, r.type, r.extraIdentity) for r in all_resources]}'
    )

    formats_found = {r.extraIdentity['sbom-format'] for r in sbom_resources}
    assert 'spdx-2.3' in formats_found, f'spdx-2.3 missing from {formats_found}'
    assert 'cyclonedx-1.6' in formats_found, f'cyclonedx-1.6 missing from {formats_found}'

    for r in sbom_resources:
        assert r.access.type is ocm.AccessType.OCI_REGISTRY, (
            f'{r.extraIdentity["sbom-format"]}: expected ociRegistry access, got {r.access.type}'
        )
        img_ref = r.access.imageReference
        assert '@sha256:' in img_ref, (
            f'{r.extraIdentity["sbom-format"]}: imageReference {img_ref!r} has no digest'
        )
        assert img_ref.startswith(tgt), (
            f'{r.extraIdentity["sbom-format"]}: imageReference {img_ref!r} '
            f'should start with {tgt!r}'
        )
        labels = {l.name: l.value for l in r.labels}
        assert 'gardener.cloud/sbom/source-image' in labels, (
            f'{r.extraIdentity["sbom-format"]}: missing source-image label'
        )
        assert 'gardener.cloud/sbom/source-image-digest' in labels, (
            f'{r.extraIdentity["sbom-format"]}: missing source-image-digest label'
        )
        logger.info(f'  {r.extraIdentity["sbom-format"]}: {img_ref}')

    logger.info('all assertions passed')


if __name__ == '__main__':
    import uuid
    run_id = os.environ.get('RUN_ID') or uuid.uuid4().hex[:8]
    logger.info(f'using {run_id=}')
    run(run_id)
