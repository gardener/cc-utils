# SPDX-FileCopyrightText: 2021 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import concurrent.futures
import dataclasses
import enum
import hashlib
import itertools
import json
import jsonschema
import logging
import os
import typing
import threading

import ccc.oci
import ci.util
import ctt.replicate
import cnudie.iter
import cnudie.retrieve
import cnudie.upload
import container.util
import cosign.payload as cp
import gci.componentmodel as cm
import oci
import oci.client
import oci.model as om

import ctt.cosign_util as cosign
import ctt.filters as filters
import ctt.processing_model as processing_model
import ctt.processors as processors
from ctt.rbsc_bom import BOMEntry, BOMEntryType
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
        component: cm.Component,
        resource: cm.Resource,
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
        component: cm.Component,
        resource: cm.Resource,
        processing_mode: ProcessingMode,
        inject_ocm_coordinates_into_oci_manifests: bool = False,
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

        job = self._processor.process(processing_job=job)

        first = True
        for uploader in self._uploaders:
            job = uploader.process(job, target_as_source=not first)
            first = False

        lssd_label = create_lssd_label(
            processing_rules=[
                self._name,
            ],
        )
        patched_resource = job.processed_resource.set_label(
            label=lssd_label,
        )
        job = dataclasses.replace(
            job,
            processed_resource=patched_resource,
        )

        return job


def create_lssd_label(
    processing_rules: typing.List[str],
) -> cm.Label:
    lssd_label_name = 'cloud.gardener.cnudie/sdo/lssd'
    label = cm.Label(
        name=lssd_label_name,
        value={
            'processingRules': processing_rules,
        },
    )

    return label


def parse_processing_cfg(path):
    raw_cfg = ci.util.parse_yaml_file(path)

    processing_cfg_dir = os.path.abspath(os.path.dirname(path))
    for name, cfg in raw_cfg.get('processors', {}).items():
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
    shared_processors: dict = {},
    shared_uploaders: dict = {},
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


def create_jobs(
    processing_cfg_path,
    component_descriptor_v2: cm.ComponentDescriptor,
    processing_mode,
    inject_ocm_coordinates_into_oci_manifests,
    component_descriptor_lookup: cnudie.retrieve.ComponentDescriptorLookupById,
):
    processing_cfg = parse_processing_cfg(processing_cfg_path)

    shared_processors = {
        name: _processor(cfg) for name, cfg in processing_cfg.get('processors', {}).items()
    }
    shared_uploaders = {
        name: _uploader(cfg) for name, cfg in processing_cfg.get('uploaders', {}).items()
    }

    for component, resource in cnudie.iter.iter_resources(
        component=component_descriptor_v2,
        lookup=component_descriptor_lookup,
    ):
        resource: cm.Resource
        # XXX only support OCI-resources for now
        if not resource.type is cm.ArtefactType.OCI_IMAGE:
            continue

        oci_resource = resource
        for pipeline in enum_processing_cfgs(
            parse_processing_cfg(processing_cfg_path),
            shared_processors,
            shared_uploaders,
        ):
            job = pipeline.process(
                component=component,
                resource=oci_resource,
                processing_mode=processing_mode,
                inject_ocm_coordinates_into_oci_manifests=inject_ocm_coordinates_into_oci_manifests,
            )

            if not job:
                continue  # pipeline did not want to process

            yield job
            break
        else:
            ci.util.warning(
                f' no matching processor: {component.name}:{oci_resource.access}'
            )


uploaded_image_refs_to_digests = {}  # <ref>:<digest>
uploaded_image_refs_to_ready_events = {}  # <ref>:<event> (set if digest is available)
upload_image_lock = threading.Lock()


# uploads a single OCI artifact and returns the content digest
def process_upload_request(
    processing_job: processing_model.ProcessingJob,
    replication_mode=oci.ReplicationMode.PREFER_MULTIARCH,
    platform_filter: typing.Callable[[om.OciPlatform], bool] = None,
    oci_client: oci.client.Client = None,
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
    # other threads waiting for upload result that result is ready be setting the event

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

    _, _, raw_manifest = container.util.filter_image(
        source_ref=src_ref,
        target_ref=tgt_ref,
        remove_files=upload_request.remove_files,
        mode=replication_mode,
        platform_filter=platform_filter,
        oci_client=oci_client,
        oci_manifest_annotations=oci_manifest_annotations,
    )

    logger.info(f'finished processing {src_ref} -> {tgt_ref=}')

    manifest_digest = hashlib.sha256(raw_manifest).hexdigest()
    uploaded_image_refs_to_digests[tgt_ref] = f'sha256:{manifest_digest}'
    upload_done_event.set()
    return f'sha256:{manifest_digest}'


def set_digest(image_reference: str, docker_content_digest: str) -> str:
    last_part = image_reference.split('/')[-1]
    if '@' in last_part:
        src_name, _ = image_reference.rsplit('@', 1)
    else:
        src_name, _ = image_reference.rsplit(':', 1)

    return f'{src_name}@{docker_content_digest}'


def labels_with_original_tag(
    resource: cm.Resource,
    src_ref: str,
) -> typing.Sequence[cm.Label]:
    last_part = src_ref.split('/')[-1]
    if '@' in last_part:
        raise RuntimeError(
            f'cannot extract tag from resource that is referenced via digest. {resource=}'
        )

    _, src_tag = src_ref.rsplit(':', 1)
    original_tag_label = cm.Label(
        name=original_tag_label_name,
        value=src_tag,
    )
    src_labels = resource.labels or []
    return ctt_util.add_label(
        src_labels=src_labels,
        label=original_tag_label,
    )


def access_resource_via_digest(res: cm.Resource, docker_content_digest: str) -> cm.Resource:
    if res.access.type is cm.AccessType.OCI_REGISTRY:
        updated_labels = labels_with_original_tag(res, res.access.imageReference)
        digest_ref = set_digest(res.access.imageReference, docker_content_digest)
        # pylint: disable-next=too-many-function-args
        digest_access = cm.OciAccess(
            imageReference=digest_ref,
        )
    elif res.access.type is cm.AccessType.RELATIVE_OCI_REFERENCE:
        updated_labels = labels_with_original_tag(res, res.access.reference)
        digest_ref = set_digest(res.access.reference, docker_content_digest)
        digest_access = cm.RelativeOciAccess(
            reference=digest_ref
        )
    else:
        raise NotImplementedError

    return dataclasses.replace(
        res,
        access=digest_access,
        labels=updated_labels,
    )


def process_images(
    processing_cfg_path,
    component_descriptor_v2,
    tgt_ctx_base_url: str,
    component_descriptor_lookup: cnudie.retrieve.ComponentDescriptorLookupById,
    processing_mode=ProcessingMode.REGULAR,
    upload_mode=None,
    upload_mode_cd=None,
    upload_mode_images=None,
    replication_mode=oci.ReplicationMode.PREFER_MULTIARCH,
    inject_ocm_coordinates_into_oci_manifests=False,
    skip_cd_validation=False,
    generate_cosign_signatures=False,
    cosign_repository=None,
    signing_server_url=None,
    root_ca_cert_path=None,
    platform_filter: typing.Callable[[om.OciPlatform], bool] = None,
    bom_resources: typing.Sequence[BOMEntry] = [],
    skip_component_upload: typing.Callable[[cm.Component], bool] = None,
    oci_client: oci.client.Client = None,
):
    if not oci_client:
        oci_client = ccc.oci.oci_client()

    if processing_mode is ProcessingMode.DRY_RUN:
        ci.util.warning('dry-run: not downloading or uploading any images')

    if upload_mode_images:
        logger.warn('passing upload_mode_images is deprecated - will ignore setting')

    if upload_mode:
        logger.warn('passing upload_mode is deprected - will ignore setting')

    src_ctx_base_url = component_descriptor_v2.component.current_repository_ctx().baseUrl

    if src_ctx_base_url == tgt_ctx_base_url:
        raise RuntimeError('current repo context and target repo context must be different!')

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=16)

    jobs = create_jobs(
        processing_cfg_path,
        component_descriptor_v2=component_descriptor_v2,
        processing_mode=processing_mode,
        inject_ocm_coordinates_into_oci_manifests=inject_ocm_coordinates_into_oci_manifests,
        component_descriptor_lookup=component_descriptor_lookup,
    )

    def process_job(processing_job: processing_model.ProcessingJob):
        if processing_mode is ProcessingMode.DRY_RUN:
            return processing_job
        elif processing_mode is ProcessingMode.REGULAR:
            pass
        else:
            raise NotImplementedError(processing_mode)

        docker_content_digest = process_upload_request(
            processing_job=processing_job,
            replication_mode=replication_mode,
            platform_filter=platform_filter,
            oci_client=oci_client,
        )

        if not docker_content_digest:
            raise RuntimeError(f'No Docker_Content_Digest returned for {processing_job=}')

        if generate_cosign_signatures:
            digest_ref = set_digest(
                processing_job.upload_request.target_ref,
                docker_content_digest,
            )
            cosign_sig_ref = cosign.calc_cosign_sig_ref(image_ref=digest_ref)

            unsigned_payload = cp.Payload(
                image_ref=digest_ref,
            ).normalised_json()
            hash = hashlib.sha256(unsigned_payload.encode())
            digest = hash.digest()
            signature = ctt_util.sign_with_signing_server(
                server_url=signing_server_url,
                content=digest,
                root_ca_cert_path=root_ca_cert_path,
            )

            # cosign every time appends the signature in the signature oci artifact, even if
            # the exact same signature already exists there. therefore, check if the exact same
            # signature already exists
            signature_exists = False

            # accept header should not be needed here as we are referencing the manifest
            # via digest.
            # but set just for safety reasons.
            accept = replication_mode.accept_header()
            manifest_blob_ref = oci_client.head_manifest(
                image_reference=cosign_sig_ref,
                absent_ok=True,
                accept=accept,
            )

            if bool(manifest_blob_ref):
                cosign_sig_manifest = oci_client.manifest(cosign_sig_ref)
                for layer in cosign_sig_manifest.layers:
                    existing_signature = layer.annotations.get(
                        "dev.cosignproject.cosign/signature",
                        "",
                    )
                    if existing_signature == signature:
                        signature_exists = True
                        break

            if not signature_exists:
                cosign.attach_signature(
                    image_ref=digest_ref,
                    unsigned_payload=unsigned_payload.encode(),
                    signature=signature.encode(),
                    cosign_repository=cosign_repository,
                )
            else:
                logger.info(
                    f'{digest_ref=} - signature for manifest already exists '
                    '- skipping signature upload'
                )
        processed_resource = processing_job.processed_resource

        if processed_resource and (digest := processed_resource.digest):
            # if resource has a digest we understand, and is an ociArtifact, then we need to
            # update the digest, because we might have changed the oci-artefact
            if digest.hashAlgorithm.upper() == 'SHA-256' and \
              digest.normalisationAlgorithm == 'ociArtifactDigest/v1':
                digest.value = docker_content_digest.removeprefix('sha256:')

                processed_resource = dataclasses.replace(
                    processed_resource,
                    digest=digest,
                )

        if processing_job.upload_request.reference_target_by_digest:
            processing_job.upload_request = dataclasses.replace(
                processing_job.upload_request,
                target_ref=set_digest(
                    processing_job.upload_request.target_ref,
                    docker_content_digest,
                ),
            )

        bom_resources.append(
            BOMEntry(
                processing_job.upload_request.target_ref,
                BOMEntryType.Docker,
                f'{processing_job.component.name}/{processing_job.resource.name}',
            )
        )

        return processing_job

    jobs = executor.map(process_job, jobs)

    # group jobs by component-version (TODO: either make Component immutable, or implement
    # __eq__ / __hash__
    def cname_version(component):
        return (component.name, component.version)

    def job_cname_version(job: processing_model.ProcessingJob):
        return cname_version(job.component)

    def append_ctx_repo(ctx_base_url, component):
        if isinstance(ctx_base_url, str):
            ocm_repo = cm.OciOcmRepository(baseUrl=ctx_base_url)
        elif isinstance(ctx_base_url, cm.OciOcmRepository):
            ocm_repo = ctx_base_url
        else:
            raise TypeError(ctx_base_url)

        if component.current_repository_ctx().baseUrl != ctx_base_url:
            component.repositoryContexts.append(
                ocm_repo,
            )

    components = []
    for _, job_group in itertools.groupby(
        sorted(jobs, key=job_cname_version),
        job_cname_version,
    ):

        patched_resources = {}

        # patch-in overwrites (caveat: must be done sequentially, as lists are not threadsafe)
        for job in job_group:
            component = job.component
            patched_resource = job.processed_resource or job.resource
            patched_resources[job.resource.identity(component.resources)] = patched_resource
            continue

        res_list = []
        for res in component.resources:
            if res.identity(component.resources) in patched_resources:
                res_list.append(patched_resources[res.identity(component.resources)])
            else:
                res_list.append(res)

        components.append(dataclasses.replace(
            component,
            resources=res_list,
        ))

    processed_component_versions = {cname_version(c) for c in components}

    # hack: add all components w/o resources (those would otherwise be ignored)
    for component_node in cnudie.iter.iter(
        component=component_descriptor_v2,
        lookup=component_descriptor_lookup,
        node_filter=cnudie.iter.Filter.components,
    ):
        component = component_node.component
        if not cname_version(component) in processed_component_versions:
            components.append(component)
            processed_component_versions.add(cname_version(component))

    root = component_descriptor_v2.component

    for component in components:
        # root component descriptor is typically not uploaded prior to calling CTT
        # -> use passed-in component-descriptor
        if component.name == root.name and component.version == root.version:
            src_component = root
        else:
            src_component = component_descriptor_lookup(component).component
        src_ocm_repo = src_component.current_repository_ctx()
        append_ctx_repo(src_ocm_repo, component)
        append_ctx_repo(tgt_ctx_base_url, component)

        ocm_repository = component.current_repository_ctx()
        oci_ref = ocm_repository.component_version_oci_ref(component)

        bom_resources.append(
            BOMEntry(
                oci_ref,
                BOMEntryType.Docker,
                component.name,
            )
        )

    source_comp = component_descriptor_v2.component

    # publish the (patched) component-descriptors
    def reupload_component(component: cm.Component):
        if skip_component_upload and skip_component_upload(component):
            return

        component_descriptor = dataclasses.replace(
            component_descriptor_v2,
            component=component,
        )

        # Validate the patched component-descriptor and exit on fail
        if not skip_cd_validation:
            # ensure component-descriptor is json-serialisable
            raw = dataclasses.asdict(component_descriptor)
            try:
                raw_json = json.dumps(raw, cls=ctt_util.EnumJSONEncoder)
            except Exception as e:
                logger.error(
                    f'Component-Descriptor could not be json-serialised: {e}'
                )
                raise
            try:
                raw = json.loads(raw_json)
            except Exception as e:
                logger.error(
                    f'Component-Descriptor could not be deserialised: {e}'
                )
                raise

            try:
                cm.ComponentDescriptor.validate(raw, validation_mode=cm.ValidationMode.FAIL)
            except jsonschema.exceptions.RefResolutionError as rre:
                logger.warning(
                    'error whilst resolving reference from json-schema (see below) - will ignore'
                )
                print(rre)
            except Exception as e:
                c = component_descriptor.component
                component_id = f'{c.name}:{c.version}'
                logger.error(
                    f'Schema validation for component-descriptor {component_id} failed with {e}'
                )
                raise

        if processing_mode is ProcessingMode.REGULAR:
            if component.name == source_comp.name and component.version == source_comp.version:
                # we must differentiate whether the input component descriptor (1) exists in the
                # source context or (2) not (e.g. if a component descriptor from a local
                # file is used).
                # for case (2) the copying of resources isn't supported by the coding.
                if component_descriptor_lookup(component_descriptor, absent_ok=True):
                    cd_exists_in_src_ctx = True
                else:
                    cd_exists_in_src_ctx = False

                if cd_exists_in_src_ctx:
                    orig_ocm_repo = component.repositoryContexts[-2]
                    ctt.replicate.replicate_oci_artifact_with_patched_component_descriptor(
                        src_name=component_descriptor.component.name,
                        src_version=component_descriptor.component.version,
                        patched_component_descriptor=component_descriptor,
                        src_ctx_repo=orig_ocm_repo,
                    )
                else:
                    if component.resources:
                        raise NotImplementedError('cannot replicate resources of root component')
                    cnudie.upload.upload_component_descriptor(
                        component_descriptor=component_descriptor,
                        ocm_repository=tgt_ctx_base_url,
                        on_exist=cnudie.upload.UploadMode.SKIP,
                    )
            else:
                orig_ocm_repo = component.repositoryContexts[-2]
                ctt.replicate.replicate_oci_artifact_with_patched_component_descriptor(
                    src_name=component_descriptor.component.name,
                    src_version=component_descriptor.component.version,
                    patched_component_descriptor=component_descriptor,
                    src_ctx_repo=orig_ocm_repo,
                )
        elif processing_mode == ProcessingMode.DRY_RUN:
            print('dry-run - will not publish component-descriptor')
            return
        else:
            raise NotImplementedError(processing_mode)

    for _ in executor.map(reupload_component, components):
        pass

    # find the original component (yes, this is hacky / cumbersome)
    original_comp = [
        c for c in components
        if c.name == source_comp.name and c.version == source_comp.version
    ]
    if not (leng := len(original_comp)) == 1:
        if leng < 1:
            raise RuntimeError(f'did not find {source_comp.name=} - this is a bug!')
        if leng > 1:
            raise RuntimeError(f'found more than one version of {source_comp.name=} - pbly a bug!')

    return dataclasses.replace(
        component_descriptor_v2,
        component=original_comp[0],  # safe, because we check for leng above
    )
