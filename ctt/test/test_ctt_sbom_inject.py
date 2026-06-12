#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
'''
Integration test for CTT SBOM injection.

Constructs a synthetic three-component OCM tree, pushes its component descriptors to a
per-run source prefix, runs the full CTT replication pipeline with SbomInjectionProcessor,
and asserts SPDX + CycloneDX resources were injected for every OCI image resource.

Cleanup of source + destination artefacts is attempted at the end (best-effort).

Environment:
  RUN_ID   override the run-id used for registry path isolation (default: generated uuid4 hex)

  Registry credentials are read from ~/.docker/config.json (base64 auth entries).
  The credHelpers entry for europe-docker.pkg.dev is intentionally stripped so that
  syft can use the static credentials without requiring an interactive gcloud session.
'''
import dataclasses
import hashlib
import json
import logging
import os
import sys
import tempfile

import yaml

import oci.auth as oa
import oci.client as oc
import oci.model as om
import ocm
import ocm.oci
import ocm.iter
import cnudie.retrieve
import ctt.process_dependencies as pdeps

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger('ctt-sbom-inttest')

_REGISTRY = 'europe-docker.pkg.dev/gardener-project/snapshots'
_INTTEST_PREFIX = 'cicd/integrationtest'

# public images used as resource targets (small, well-known)
_ALPINE_REF = 'alpine:3.21'
_WOLFI_REF = 'cgr.dev/chainguard/wolfi-base:latest'
_BUSYBOX_REF = 'busybox:stable'

# synthetic component names/versions
_ROOT_NAME = 'github.com/gardener/test/ctt-sbom-inttest/root'
_CHILD_A_NAME = 'github.com/gardener/test/ctt-sbom-inttest/child-a'
_CHILD_B_NAME = 'github.com/gardener/test/ctt-sbom-inttest/child-b'
_VERSION = 'v0.0.0-inttest'


def _make_resource(name: str, image_ref: str) -> ocm.Resource:
    return ocm.Resource(
        name=name,
        version=_VERSION,
        type='ociImage',
        relation=ocm.ResourceRelation.EXTERNAL,
        access=ocm.OciAccess(imageReference=image_ref),
    )


def _make_component(
    name: str,
    ocm_repo: ocm.OciOcmRepository,
    resources: list[ocm.Resource],
    refs: list[ocm.ComponentReference] | None = None,
) -> ocm.ComponentDescriptor:
    component = ocm.Component(
        name=name,
        version=_VERSION,
        repositoryContexts=[ocm_repo],
        provider='gardener',
        sources=[],
        componentReferences=refs or [],
        resources=resources,
    )
    return ocm.ComponentDescriptor(
        meta=ocm.Metadata(schemaVersion=ocm.SchemaVersion.V2),
        component=component,
    )


def push_component_descriptor(
    component_descriptor: ocm.ComponentDescriptor,
    oci_client: oc.Client,
) -> str:
    '''
    Push a component descriptor as a fresh OCI artefact.  Returns the pushed image reference.
    '''
    repo: ocm.OciOcmRepository = component_descriptor.component.current_ocm_repo
    image_ref = repo.component_version_oci_ref(component_descriptor.component)

    tar_fobj = ocm.oci.component_descriptor_to_tarfileobj(component_descriptor)
    tar_bytes = tar_fobj.read()

    cd_digest = f'sha256:{hashlib.sha256(tar_bytes).hexdigest()}'
    cd_size = len(tar_bytes)

    cfg = ocm.oci.ComponentDescriptorOciCfg(
        componentDescriptorLayer=ocm.oci.ComponentDescriptorOciBlobRef(
            digest=cd_digest,
            size=cd_size,
            mediaType=ocm.oci.component_descriptor_mimetype,
        ),
    )
    cfg_bytes = json.dumps(dataclasses.asdict(cfg)).encode('utf-8')
    cfg_digest = f'sha256:{hashlib.sha256(cfg_bytes).hexdigest()}'
    cfg_size = len(cfg_bytes)

    oci_client.put_blob(
        image_reference=image_ref,
        digest=cd_digest,
        octets_count=cd_size,
        data=tar_bytes,
        mimetype=ocm.oci.component_descriptor_mimetype,
    )
    oci_client.put_blob(
        image_reference=image_ref,
        digest=cfg_digest,
        octets_count=cfg_size,
        data=cfg_bytes,
        mimetype=ocm.oci.component_descriptor_cfg_mimetype,
    )

    manifest = om.OciImageManifest(
        config=om.OciBlobRef(
            digest=cfg_digest,
            mediaType=ocm.oci.component_descriptor_cfg_mimetype,
            size=cfg_size,
        ),
        layers=[om.OciBlobRef(
            digest=cd_digest,
            mediaType=ocm.oci.component_descriptor_mimetype,
            size=cd_size,
        )],
    )
    manifest_bytes = json.dumps(manifest.as_dict()).encode('utf-8')

    oci_client.put_manifest(
        image_reference=image_ref,
        manifest=manifest_bytes,
    )

    return image_ref


def _purge_ref(oci_client: oc.Client, image_ref: str):
    try:
        oci_client.delete_manifest(
            image_reference=image_ref,
            purge=True,
            absent_ok=True,
        )
    except Exception as e:
        logger.warning(f'cleanup: failed to delete {image_ref}: {e}')


def run(run_id: str):
    oci_client = oc.Client(
        credentials_lookup=oa.docker_credentials_lookup(),
    )

    run_prefix = f'{_REGISTRY}/{_INTTEST_PREFIX}/{run_id}'
    src_repo_url = f'{run_prefix}/src'
    dst_repo_url = f'{run_prefix}/dst'

    src_ocm_repo = ocm.OciOcmRepository(baseUrl=src_repo_url)
    dst_ocm_repo = ocm.OciOcmRepository(baseUrl=dst_repo_url)

    # --- build synthetic component tree ---
    child_a_cd = _make_component(
        name=_CHILD_A_NAME,
        ocm_repo=src_ocm_repo,
        resources=[_make_resource('wolfi-base', _WOLFI_REF)],
    )
    child_b_cd = _make_component(
        name=_CHILD_B_NAME,
        ocm_repo=src_ocm_repo,
        resources=[_make_resource('busybox', _BUSYBOX_REF)],
    )
    root_cd = _make_component(
        name=_ROOT_NAME,
        ocm_repo=src_ocm_repo,
        resources=[_make_resource('alpine', _ALPINE_REF)],
        refs=[
            ocm.ComponentReference(
                name='child-a',
                componentName=_CHILD_A_NAME,
                version=_VERSION,
            ),
            ocm.ComponentReference(
                name='child-b',
                componentName=_CHILD_B_NAME,
                version=_VERSION,
            ),
        ],
    )

    # --- push source component descriptors ---
    src_refs = []
    for cd in (child_a_cd, child_b_cd, root_cd):
        ref = push_component_descriptor(cd, oci_client)
        src_refs.append(ref)
        logger.info(f'pushed src CD: {ref}')

    src_lookup = cnudie.retrieve.oci_component_descriptor_lookup(
        ocm_repository_lookup=cnudie.retrieve.ocm_repository_lookup(src_repo_url),
        oci_client=oci_client,
    )
    dst_lookup = cnudie.retrieve.oci_component_descriptor_lookup(
        ocm_repository_lookup=cnudie.retrieve.ocm_repository_lookup(dst_repo_url),
        oci_client=oci_client,
    )

    processing_cfg = {
        'targets': {
            'default': {
                'type': 'RegistriesTarget',
                'kwargs': {
                    'registries': [dst_repo_url],
                    'ocm_repository': dst_repo_url,
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
                    'remove_prefixes': [
                        'docker.io',
                        'index.docker.io',
                        'cgr.dev',
                        'registry-1.docker.io',
                    ],
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

    with tempfile.TemporaryDirectory() as tmpdir:
        cfg_path = os.path.join(tmpdir, 'processing.cfg')
        with open(cfg_path, 'w') as f:
            yaml.safe_dump(processing_cfg, f)

        os.environ['TMPDIR'] = tmpdir
        parsed_cfg = pdeps.parse_processing_cfg(cfg_path)

        replication_plan_step = pdeps.create_replication_plan_step(
            processing_cfg=parsed_cfg,
            root_component_descriptor=root_cd,
            src_component_descriptor_lookup=src_lookup,
            tgt_component_descriptor_lookup=dst_lookup,
            ocm_repository=dst_repo_url,
            tgt_oci_registries=[dst_repo_url],
            oci_client=oci_client,
        )

        logger.info(replication_plan_step)

        nodes = list(pdeps.process_replication_plan_step(
            replication_plan_step=replication_plan_step,
            root_component_descriptor=root_cd,
            oci_client=oci_client,
            tgt_component_descriptor_lookup=dst_lookup,
            skip_cd_validation=True,
        ))

    # --- collect pushed refs for cleanup ---
    dst_image_refs = []

    # component descriptor refs (deterministic)
    for name in (_ROOT_NAME, _CHILD_A_NAME, _CHILD_B_NAME):
        cd_ref = dst_ocm_repo.component_version_oci_ref(name=name, version=_VERSION)
        dst_image_refs.append(cd_ref)

    # image resource refs from replicated nodes
    resource_nodes = [n for n in nodes if ocm.iter.Filter.resources(n)]
    all_resources = [n.resource for n in resource_nodes]

    for resource in all_resources:
        if resource.access and hasattr(resource.access, 'imageReference'):
            dst_image_refs.append(resource.access.imageReference)

    # --- assertions ---
    sbom_resources = [
        r for r in all_resources
        if isinstance(r.extraIdentity, dict) and r.extraIdentity.get('sbom-format')
    ]
    oci_image_resources = [
        r for r in all_resources
        if r.access.type is ocm.AccessType.OCI_REGISTRY
        and not (isinstance(r.extraIdentity, dict) and r.extraIdentity.get('sbom-format'))
    ]

    logger.info(
        f'{len(oci_image_resources)} OCI image resource(s) replicated, '
        f'{len(sbom_resources)} SBOM resource(s) injected'
    )

    assert sbom_resources, (
        f'no SBOM resources injected; all resources: '
        f'{[(r.name, r.type, r.extraIdentity) for r in all_resources]}'
    )

    # expect 2 SBOM resources per OCI image resource
    assert len(sbom_resources) == len(oci_image_resources) * 2, (
        f'expected {len(oci_image_resources) * 2} SBOM resources '
        f'(2 per image), got {len(sbom_resources)}'
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
        assert img_ref.startswith(dst_repo_url), (
            f'{r.extraIdentity["sbom-format"]}: imageReference {img_ref!r} '
            f'should start with {dst_repo_url!r}'
        )
        labels = {l.name: l.value for l in r.labels}
        assert 'gardener.cloud/sbom/source-image' in labels, (
            f'{r.extraIdentity["sbom-format"]}: missing source-image label'
        )
        assert 'gardener.cloud/sbom/source-image-digest' in labels, (
            f'{r.extraIdentity["sbom-format"]}: missing source-image-digest label'
        )
        fmt = r.extraIdentity['sbom-format']
        logger.info(f'  {r.name} ({fmt}): {img_ref}')

    logger.info('all assertions passed')

    # --- best-effort cleanup ---
    logger.info('cleaning up pushed artefacts (best-effort)...')
    for ref in src_refs + dst_image_refs:
        _purge_ref(oci_client, ref)


if __name__ == '__main__':
    import uuid
    run_id = os.environ.get('RUN_ID') or uuid.uuid4().hex[:8]
    logger.info(f'using {run_id=}')
    run(run_id)
