# SPDX-FileCopyrightText: 2021 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import collections.abc
import concurrent.futures
import dataclasses
import enum
import hashlib
import itertools
import json
import jsonschema
import logging
import os
import threading

import ccc.delivery
import ccc.oci
import ci.util
import ctt.replicate
import cnudie.iter
import cnudie.retrieve
import container.util
import dso.labels
import ocm
import oci
import oci.client
import oci.model as om

import ctt.filters as filters
import ctt.processing_model as processing_model
import ctt.processors as processors
import ctt.uploaders as uploaders
import ctt.util as ctt_util

original_tag_label_name = 'cloud.gardener.cnudie/migration/original_tag'

logger = logging.getLogger(__name__)

own_dir = os.path.abspath(os.path.dirname(__file__))


class ProcessingMode(enum.Enum):
    REGULAR = 'regular'
    DRY_RUN = 'dry_run'


class ProcessingPipeline:
    def __init__(
        self,
        name,
        filters,
        processor,
        uploaders,
    ):
        self._name = name
        self._filters = filters
        self._processor = processor
        self._uploaders = uploaders

    def matches(
        self,
        component: ocm.Component,
        resource: ocm.Resource,
    ):
        filters_count = len(self._filters)
        return all(
            map(
                lambda filtr, component, resource: filtr.matches(component, resource),
                self._filters,
                itertools.repeat(component, filters_count),
                itertools.repeat(resource, filters_count),
            )
        )

    def process(
        self,
        component: ocm.Component,
        resource: ocm.Resource,
        inject_ocm_coordinates_into_oci_manifests: bool=False,
    ) -> processing_model.ProcessingJob:
        if not self.matches(component, resource):
            return None

        logging.info(f'{inject_ocm_coordinates_into_oci_manifests=}')
        logging.info(
            f'{self._name} will process: '
            f'{component.name}:{resource.type}:{resource.access}'
        )

        job = processing_model.ProcessingJob(
            component=component,
            resource=resource,
            upload_request=processing_model.ContainerImageUploadRequest(
                source_ref=None,
                target_ref=None,  # must be set by a later step
                remove_files=None,  # _may_ be set by a later step
            ),
            inject_ocm_coordinates_into_oci_manifest=inject_ocm_coordinates_into_oci_manifests,
        )

        job: processing_model.ProcessingJob = self._processor.process(processing_job=job)

        first = True
        for uploader in self._uploaders:
            job = uploader.process(job, target_as_source=not first)
            first = False

        ctt_label = create_ctt_label(
            processing_rules=[
                self._name,
            ],
        )
        patched_resource = job.processed_resource.set_label(
            label=ctt_label,
        )
        job = dataclasses.replace(
            job,
            processed_resource=patched_resource,
        )

        return job


def create_ctt_label(
    processing_rules: list[str],
) -> ocm.Label:
    ctt_label_name = 'cloud.gardener/ctt-hint'
    label = ocm.Label(
        name=ctt_label_name,
        value={
            'processingRules': processing_rules,
        },
    )

    return label


def parse_processing_cfg(path: str):
    raw_cfg = ci.util.parse_yaml_file(path)

    processing_cfg_dir = os.path.abspath(os.path.dirname(path))
    for _, cfg in raw_cfg.get('processors', {}).items():
        cfg['kwargs']['base_dir'] = processing_cfg_dir

    return raw_cfg


def _filter(filter_cfg: dict):
    filter_ctor = getattr(filters, filter_cfg['type'])
    filter_ = filter_ctor(**filter_cfg.get('kwargs', {}))

    return filter_


def _processor(processor_cfg: dict):
    proc_type = processor_cfg['type']
    proc_ctor = getattr(processors, proc_type, None)
    if not proc_ctor:
        ci.util.fail(f'no such image processor: {proc_type}')
    processor = proc_ctor(**processor_cfg.get('kwargs', {}))
    return processor


def _uploader(uploader_cfg: dict):
    upload_type = uploader_cfg['type']
    upload_ctor = getattr(uploaders, upload_type, None)
    if not upload_ctor:
        ci.util.fail(f'no such uploader: {upload_type}')
    uploader = upload_ctor(**uploader_cfg.get('kwargs', {}))
    return uploader


def processing_pipeline(
    processing_cfg: dict,
    shared_processors: dict={},
    shared_uploaders: dict={},
) -> ProcessingPipeline:
    name = processing_cfg.get('name', '<no name>')

    filter_cfgs = processing_cfg['filter']
    if isinstance(filter_cfgs, dict):
        filter_cfgs = [filter_cfgs]
    filters = [_filter(filter_cfg=filter_cfg) for filter_cfg in filter_cfgs]

    if 'processor' in processing_cfg:
        processor_cfg = processing_cfg['processor']
        if isinstance(processor_cfg, str):
            proc = shared_processors[processor_cfg]
        else:
            proc = _processor(processor_cfg=processor_cfg)
    else:
        proc = processors.NoOpProcessor()

    upload_cfgs = processing_cfg['upload']
    if not isinstance(upload_cfgs, list):
        upload_cfgs = [upload_cfgs]  # normalise to list

    def instantiate_uploader(upload_cfg):
        if isinstance(upload_cfg, str):
            return shared_uploaders[upload_cfg]
        return _uploader(upload_cfg)

    uploaders = [instantiate_uploader(upload_cfg) for upload_cfg in upload_cfgs]

    pipeline = ProcessingPipeline(
        name=name,
        filters=filters,
        processor=proc,
        uploaders=uploaders,
    )
    return pipeline


def enum_processing_cfgs(
    processing_cfg: dict,
    shared_processors: dict,
    shared_uploaders: dict,
):
    cfg_entries = processing_cfg['image_processing_cfg']

    yield from map(
        processing_pipeline,
        cfg_entries,
        itertools.repeat(shared_processors, len(cfg_entries)),
        itertools.repeat(shared_uploaders, len(cfg_entries)),
    )


def determine_changed_components(
    component_descriptor: ocm.ComponentDescriptor,
    tgt_ocm_repo_url: str,
    component_descriptor_lookup: cnudie.retrieve.ComponentDescriptorLookupById,
    tgt_component_descriptor_lookup: cnudie.retrieve.ComponentDescriptorLookupById,
    component_filter: collections.abc.Callable[[ocm.Component], bool]=None,
    reftype_filter: collections.abc.Callable[[cnudie.iter.NodeReferenceType], bool]=None,
) -> collections.abc.Generator[ocm.ComponentDescriptor, None, None]:
    component = component_descriptor.component

    if component_filter and component_filter(component):
        return

    if tgt_component_descriptor_lookup(
        component.identity(),
        absent_ok=True,
    ):
        logger.info(
            f'{component.identity()} already exists in {tgt_ocm_repo_url=} '
            '- skipping replication of transitive closure'
        )
        return

    for cref in component.componentReferences:
        referenced_component_descriptor = component_descriptor_lookup(ocm.ComponentIdentity(
            name=cref.componentName,
            version=cref.version,
        ))

        yield from determine_changed_components(
            component_descriptor=referenced_component_descriptor,
            tgt_ocm_repo_url=tgt_ocm_repo_url,
            component_descriptor_lookup=component_descriptor_lookup,
            tgt_component_descriptor_lookup=tgt_component_descriptor_lookup,
            component_filter=component_filter,
            reftype_filter=reftype_filter,
        )

    if not (
        reftype_filter and reftype_filter(cnudie.iter.NodeReferenceType.EXTRA_COMPONENT_REFS_LABEL)
    ) and (
        label := component.find_label(dso.labels.ExtraComponentReferencesLabel.name)
    ):
        extra_crefs_label = dso.labels.deserialise_label(label)

        for extra_cref in extra_crefs_label.value:
            extra_cref_id = extra_cref.component_reference
            referenced_component_descriptor = component_descriptor_lookup(extra_cref_id)

            yield from determine_changed_components(
                component_descriptor=referenced_component_descriptor,
                tgt_ocm_repo_url=tgt_ocm_repo_url,
                component_descriptor_lookup=component_descriptor_lookup,
                tgt_component_descriptor_lookup=tgt_component_descriptor_lookup,
                component_filter=component_filter,
                reftype_filter=reftype_filter,
            )

    yield component_descriptor


def create_jobs(
    component_descriptors: collections.abc.Iterable[ocm.ComponentDescriptor],
    processing_cfg: dict,
    inject_ocm_coordinates: bool,
) -> collections.abc.Generator[processing_model.ProcessingJob, None, None]:
    shared_processors = {
        name: _processor(cfg) for name, cfg in processing_cfg.get('processors', {}).items()
    }
    shared_uploaders = {
        name: _uploader(cfg) for name, cfg in processing_cfg.get('uploaders', {}).items()
    }

    for component_descriptor in component_descriptors:
        component = component_descriptor.component

        for resource in component.resources:
            # XXX only support OCI-resources for now
            if not resource.access.type is ocm.AccessType.OCI_REGISTRY:
                continue

            for pipeline in enum_processing_cfgs(
                processing_cfg=processing_cfg,
                shared_processors=shared_processors,
                shared_uploaders=shared_uploaders,
            ):
                job = pipeline.process(
                    component=component,
                    resource=resource,
                    inject_ocm_coordinates_into_oci_manifests=inject_ocm_coordinates,
                )

                if not job:
                    continue # pipeline did not want to process

                yield job
                break
            else:
                ci.util.warning(
                    f'no matching processor: {component.name}:{resource.access}'
                )


uploaded_image_refs_to_digests = {}  # <ref>:<digest>
uploaded_image_refs_to_ready_events = {}  # <ref>:<event> (set if digest is available)
upload_image_lock = threading.Lock()


# uploads a single OCI artifact and returns the content digest
def process_upload_request(
    processing_job: processing_model.ProcessingJob,
    replication_mode: oci.ReplicationMode=oci.ReplicationMode.PREFER_MULTIARCH,
    platform_filter: collections.abc.Callable[[om.OciPlatform], bool]=None,
    oci_client: oci.client.Client=None,
) -> str:
    global uploaded_image_refs_to_digests
    global uploaded_image_refs_to_ready_events
    global upload_image_lock

    if not oci_client:
        oci_client = ccc.oci.oci_client()

    upload_request = processing_job.upload_request
    tgt_ref = upload_request.target_ref

    # if event is present, upload might still be in progress (done if event is "set")
    with upload_image_lock:
        if tgt_ref in uploaded_image_refs_to_ready_events:
            upload_done_event = uploaded_image_refs_to_ready_events[tgt_ref]
            wait_for_upload = True
        else:
            upload_done_event = threading.Event()
            uploaded_image_refs_to_ready_events[tgt_ref] = upload_done_event
            wait_for_upload = False

    if wait_for_upload:
        upload_done_event.wait()

    if tgt_ref in uploaded_image_refs_to_digests:  # digest already present
        logger.info(f'{tgt_ref=} - was already uploaded by another rule - skipping')
        return uploaded_image_refs_to_digests[tgt_ref]

    # most common case: tgt has not yet been processed - process and afterwards signal
    # other threads waiting for upload result that result is ready by setting the event

    accept = replication_mode.accept_header()
    manifest_blob_ref = oci_client.head_manifest(
        image_reference=tgt_ref,
        absent_ok=True,
        accept=accept,
    )
    if bool(manifest_blob_ref):
        logger.info(f'{tgt_ref=} exists - skipping upload')

        uploaded_image_refs_to_digests[tgt_ref] = manifest_blob_ref.digest
        upload_done_event.set()
        return manifest_blob_ref.digest

    src_ref = upload_request.source_ref

    logger.info(f'processing {src_ref} -> {tgt_ref=}')
    logger.info(f'{tgt_ref=} {upload_request.remove_files=} {replication_mode=} {platform_filter=}')

    component = processing_job.component
    resource = processing_job.resource

    if processing_job.inject_ocm_coordinates_into_oci_manifest:
        oci_manifest_annotations = {
            'cloud.gardener/ocm-component': f'{component.name}:{component.version}',
            'cloud.gardener/ocm-resource': f'{resource.name}:{resource.version}',
        }
    else:
        oci_manifest_annotations = None

    logging.info(f'{oci_manifest_annotations=}')

    try:
        _, patched_tgt_ref, raw_manifest = container.util.filter_image(
            source_ref=src_ref,
            target_ref=tgt_ref,
            remove_files=upload_request.remove_files,
            mode=replication_mode,
            platform_filter=platform_filter,
            oci_client=oci_client,
            oci_manifest_annotations=oci_manifest_annotations,
        )
    except Exception as e:
        logger.error(
            f'error trying to replicate {src_ref=} -> {tgt_ref=}'
        )
        e.add_note(f'filter_image: {src_ref=} -> {tgt_ref=}')
        raise e

    if tgt_ref != patched_tgt_ref:
        logger.info(f'finished processing {src_ref} -> {patched_tgt_ref=} (initial {tgt_ref=})')
    else:
        logger.info(f'finished processing {src_ref} -> {tgt_ref=}')

    manifest_digest = hashlib.sha256(raw_manifest).hexdigest()
    uploaded_image_refs_to_digests[tgt_ref] = f'sha256:{manifest_digest}'
    upload_done_event.set()
    return f'sha256:{manifest_digest}'


def process_images(
    processing_cfg_path: str,
    component_descriptor_v2: ocm.ComponentDescriptor,
    component_descriptor_lookup: cnudie.retrieve.ComponentDescriptorLookupById,
    processing_mode: ProcessingMode=ProcessingMode.REGULAR,
    replication_mode: oci.ReplicationMode=oci.ReplicationMode.PREFER_MULTIARCH,
    inject_ocm_coordinates_into_oci_manifests: bool=False,
    skip_cd_validation: bool=False,
    platform_filter: collections.abc.Callable[[om.OciPlatform], bool]=None,
    skip_component_upload: collections.abc.Callable[[ocm.Component], bool]=None,
    oci_client: oci.client.Client=None,
    component_filter: collections.abc.Callable[[ocm.Component], bool]=None,
    remove_label: collections.abc.Callable[[str], bool]=None,
    tgt_ocm_base_url: str=None,
    tgt_ctx_base_url: str=None, # deprecated -> replaced by `tgt_ocm_base_url`
) -> collections.abc.Generator[cnudie.iter.Node, None, None]:
    '''
    note: Passing a filter to prevent component descriptors from being replicated using the
    `skip_component_upload` parameter will still replicate all its resources (i.e. oci images)
    as well as referenced components. In contrast to that, passing a filter using the
    `component_filter` parameter will also exclude its resources as well as all transitive component
    references from the replication. In both cases, `True` means the respective component is
    _excluded_.
    '''
    if tgt_ctx_base_url:
        tgt_ocm_base_url = tgt_ctx_base_url

    if not tgt_ocm_base_url:
        raise ValueError(tgt_ocm_base_url)

    if not oci_client:
        oci_client = ccc.oci.oci_client()

    if processing_mode is ProcessingMode.DRY_RUN:
        ci.util.warning('dry-run: not downloading or uploading any images')

    src_ocm_base_url = component_descriptor_v2.component.current_ocm_repo.baseUrl

    if src_ocm_base_url == tgt_ocm_base_url:
        raise RuntimeError('current repo context and target repo context must be different!')

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=16)

    reftype_filter = None
    if remove_label and remove_label(dso.labels.ExtraComponentReferencesLabel.name):
        def filter_extra_component_refs(reftype: cnudie.iter.NodeReferenceType) -> bool:
            return reftype is cnudie.iter.NodeReferenceType.EXTRA_COMPONENT_REFS_LABEL

        reftype_filter = filter_extra_component_refs

    tgt_component_descriptor_lookup = cnudie.retrieve.create_default_component_descriptor_lookup(
        ocm_repository_lookup=cnudie.retrieve.ocm_repository_lookup(tgt_ocm_base_url),
        oci_client=oci_client,
        delivery_client=ccc.delivery.default_client_if_available(),
        fallback_to_service_mapping=False,
    )

    component_descriptors = tuple(determine_changed_components(
        component_descriptor=component_descriptor_v2,
        tgt_ocm_repo_url=tgt_ocm_base_url,
        component_descriptor_lookup=component_descriptor_lookup,
        tgt_component_descriptor_lookup=tgt_component_descriptor_lookup,
        component_filter=component_filter,
        reftype_filter=reftype_filter,
    ))

    jobs = create_jobs(
        component_descriptors=component_descriptors,
        processing_cfg=parse_processing_cfg(processing_cfg_path),
        inject_ocm_coordinates=inject_ocm_coordinates_into_oci_manifests,
    )

    def process_job(processing_job: processing_model.ProcessingJob):
        if processing_mode is ProcessingMode.DRY_RUN:
            return processing_job
        elif processing_mode is ProcessingMode.REGULAR:
            pass
        else:
            raise NotImplementedError(processing_mode)

        oci_manifest_digest = process_upload_request(
            processing_job=processing_job,
            replication_mode=replication_mode,
            platform_filter=platform_filter,
            oci_client=oci_client,
        )

        if extra_tags := processing_job.extra_tags:
            target_ref = om.OciImageReference(processing_job.upload_request.target_ref)
            target_repo = target_ref.ref_without_tag
            manifest_bytes = oci_client.manifest_raw(
                image_reference=f'{target_repo}@{oci_manifest_digest}',
                accept=om.MimeTypes.prefer_multiarch,
            ).content

        for extra_tag in extra_tags:
            push_target = f'{target_repo}:{extra_tag}'

            oci_client.put_manifest(
                image_reference=push_target,
                manifest=manifest_bytes,
            )

        if not oci_manifest_digest:
            raise RuntimeError(f'No oci_manifest_digest returned for {processing_job=}')

        processed_resource = processing_job.processed_resource

        if processed_resource and (digest := processed_resource.digest):
            # if resource has a digest we understand, and is an ociArtifact, then we need to
            # update the digest, because we might have changed the oci-artefact
            if (
                digest.hashAlgorithm.upper() == 'SHA-256'
                and digest.normalisationAlgorithm == ocm.NormalisationAlgorithm.OCI_ARTIFACT_DIGEST
            ):
                digest.value = oci_manifest_digest.removeprefix('sha256:')

                processed_resource = dataclasses.replace(
                    processed_resource,
                    digest=digest,
                )

        if processing_job.upload_request.reference_target_by_digest:
            target_ref = om.OciImageReference.to_image_ref(processing_job.upload_request.target_ref)

            if (
                processing_job.upload_request.retain_symbolic_tag
                and (target_ref.has_symbolical_tag or target_ref.has_mixed_tag)
            ):
                target_ref = f'{target_ref.with_symbolical_tag}@{oci_manifest_digest}'
            else:
                target_ref = f'{target_ref.ref_without_tag}@{oci_manifest_digest}'

            access = processed_resource.access
            if access.type is ocm.AccessType.OCI_REGISTRY:
                access = dataclasses.replace(
                    processed_resource.access,
                    imageReference=target_ref,
                )

            elif access.type is ocm.AccessType.RELATIVE_OCI_REFERENCE:
                access = dataclasses.replace(
                    processed_resource.access,
                    reference=om.OciImageReference.to_image_ref(
                        image_reference=target_ref,
                        normalise=False, # don't inject docker special handlings
                    ).local_ref,
                )

            processed_resource = dataclasses.replace(
                processed_resource,
                access=access,
            )

            processing_job.processed_resource = processed_resource

            processing_job.upload_request = dataclasses.replace(
                processing_job.upload_request,
                target_ref=target_ref,
            )

        return processing_job

    def wrap_process_job(processing_job: processing_model.ProcessingJob):
        try:
            return process_job(processing_job=processing_job)
        except Exception as e:
            logger.error(f'exception while processing {processing_job=}')
            raise e

    jobs = tuple(executor.map(wrap_process_job, jobs))

    def append_ocm_repo(
        component: ocm.Component,
        ocm_repo: str | ocm.OciOcmRepository,
    ):
        if isinstance(ocm_repo, str):
            ocm_repo = ocm.OciOcmRepository(baseUrl=ocm_repo)
        elif isinstance(ocm_repo, ocm.OciOcmRepository):
            pass
        else:
            raise TypeError(ocm_repo)

        if component.current_ocm_repo.baseUrl != ocm_repo.baseUrl:
            component.repositoryContexts.append(ocm_repo)

    for component_descriptor in component_descriptors:
        component = component_descriptor.component

        job_group = [
            job for job in jobs
            if job.component.identity() == component.identity()
        ]

        patched_resources = {}

        # patch-in overwrites (caveat: must be done sequentially, as lists are not threadsafe)
        for job in job_group:
            patched_resource = job.processed_resource or job.resource
            patched_resources[job.resource.identity(component.resources)] = patched_resource

        component.resources = [
            patched_resources.get(resource.identity(component.resources), resource)
            for resource in component.resources
        ]

        append_ocm_repo(
            component=component,
            ocm_repo=tgt_ocm_base_url,
        )

        if remove_label:
            component.labels = [label for label in component.labels if not remove_label(label.name)]

        # Validate the patched component-descriptor and exit on fail
        if not skip_cd_validation:
            # ensure component-descriptor is json-serialisable
            raw = dataclasses.asdict(component_descriptor)
            try:
                raw_json = json.dumps(raw, cls=ctt_util.EnumJSONEncoder)
            except Exception as e:
                logger.error(f'Component-Descriptor could not be json-serialised: {e}')
                raise
            try:
                raw = json.loads(raw_json)
            except Exception as e:
                logger.error(f'Component-Descriptor could not be deserialised: {e}')
                raise

            try:
                ocm.ComponentDescriptor.validate(raw, validation_mode=ocm.ValidationMode.FAIL)
            except jsonschema.exceptions.RefResolutionError as rre:
                logger.warning(
                    'error whilst resolving reference from json-schema (see below) - will ignore'
                )
                print(rre)
            except Exception as e:
                component_id = f'{component.name}:{component.version}'
                logger.warning(
                    f'Schema validation for component-descriptor {component_id} failed with {e}'
                )

        # publish the (patched) component-descriptors
        if skip_component_upload and skip_component_upload(component):
            continue

        if processing_mode is ProcessingMode.DRY_RUN:
            print('dry-run - will not publish component-descriptor')
            continue
        elif processing_mode is not ProcessingMode.REGULAR:
            raise NotImplementedError(processing_mode)

        if len(ocm_repos := component.repositoryContexts) >= 2:
            orig_ocm_repo = component.repositoryContexts[-2]
        elif len(ocm_repos) == 1:
            logger.warning(f'{component.name}:{component.version} has only one ocm-repository')
            logger.warning('(expected: two or more)')
            logger.warning(f'{ocm_repos=}')
            orig_ocm_repo = component.repositoryContexts[-1]
        else:
            raise RuntimeError(f'{component.name}:{component.version} has no ocm-repository')

        ctt.replicate.replicate_oci_artifact_with_patched_component_descriptor(
            src_name=component.name,
            src_version=component.version,
            patched_component_descriptor=component_descriptor,
            src_ocm_repo=orig_ocm_repo,
        )

    # retrieve component descriptor from the target registry as local descriptor might not contain
    # patched image references (if it was already existing the the target registry and thus patching
    # has been skipped)
    if not skip_component_upload or not skip_component_upload(component_descriptor_v2.component):
        component_descriptor_v2 = tgt_component_descriptor_lookup(ocm.ComponentIdentity(
            name=component_descriptor_v2.component.name,
            version=component_descriptor_v2.component.version,
        ))

    for node in cnudie.iter.iter(
        component=component_descriptor_v2,
        lookup=tgt_component_descriptor_lookup,
        component_filter=component_filter,
        reftype_filter=reftype_filter,
    ):
        if cnudie.iter.Filter.components(node):
            pass
        elif cnudie.iter.Filter.resources(node):
            node: cnudie.iter.ResourceNode

            if node.resource.access.type not in (
                ocm.AccessType.OCI_REGISTRY,
                ocm.AccessType.RELATIVE_OCI_REFERENCE,
            ):
                continue
        else:
            continue

        yield node
