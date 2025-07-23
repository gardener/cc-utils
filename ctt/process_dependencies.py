# SPDX-FileCopyrightText: 2021 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import collections.abc
import concurrent.futures
import copy
import dataclasses
import enum
import hashlib
import functools
import itertools
import json
import jsonschema
import logging
import os
import threading
import typing

import dacite

import ci.util
import ctt.replicate
import cnudie.iter
import cnudie.retrieve
import container.util
import ocm
import ocm.gardener
import oci
import oci.client
import oci.model as om

import ctt.filters as filters
import ctt.model
import ctt.processors as processors
import ctt.targets as targets
import ctt.uploaders as uploaders
import ctt.util as ctt_util

original_tag_label_name = 'cloud.gardener.cnudie/migration/original_tag'

logger = logging.getLogger(__name__)

own_dir = os.path.abspath(os.path.dirname(__file__))


class ProcessingMode(enum.Enum):
    REGULAR = 'regular'
    DRY_RUN = 'dry_run'


@functools.cache
def create_component_descriptor_lookup_for_ocm_repo(
    ocm_repo_url: str,
    oci_client: oci.client.Client | None=None,
    delivery_service_client: typing.Union['delivery.client.DeliveryServiceClient', None]=None,
) -> cnudie.retrieve.ComponentDescriptorLookupById:
    return cnudie.retrieve.create_default_component_descriptor_lookup(
        ocm_repository_lookup=cnudie.retrieve.ocm_repository_lookup(ocm_repo_url),
        oci_client=oci_client,
        delivery_client=delivery_service_client,
        fallback_to_service_mapping=False,
    )


class ProcessingPipeline:
    def __init__(
        self,
        name: str,
        targets: list[targets.TargetBase],
        filters: list[filters.FilterBase],
        processor: processors.ProcessorBase,
        uploaders: list[uploaders.UploaderBase],
    ):
        self._name = name
        self._targets = targets
        self._filters = filters
        self._processor = processor
        self._uploaders = uploaders

    def matches_filter(
        self,
        component: ocm.Component,
        resource: ocm.Resource,
    ) -> bool:
        return all(
            filter.matches(component, resource)
            for filter in self._filters
        )

    def matches_target(
        self,
        tgt_oci_registry: str,
    ) -> bool:
        return any(
            target.filter(tgt_oci_registry)
            for target in self._targets
        )

    def process(
        self,
        component: ocm.Component,
        resource: ocm.Resource,
        tgt_oci_registry: str,
        oci_client: oci.client.Client,
        replication_mode: oci.ReplicationMode=oci.ReplicationMode.PREFER_MULTIARCH,
    ) -> ctt.model.ReplicationResourceElement | None:
        if (
            not self.matches_filter(component, resource)
            or not self.matches_target(tgt_oci_registry)
        ):
            return None

        logger.debug(
            f'{self._name} will process: {component.name}:{resource.type}:{resource.access} '
            f'{tgt_oci_registry=}'
        )

        # create copies to not unintentionally modify mutually/afterwards
        replication_resource_element = ctt.model.ReplicationResourceElement(
            source=copy.deepcopy(resource),
            target=copy.deepcopy(resource),
            component_id=component.identity(),
        )

        replication_resource_element = self._processor.process(replication_resource_element)

        first = True
        for uploader in self._uploaders:
            replication_resource_element = uploader.process(
                replication_resource_element,
                tgt_oci_registry=tgt_oci_registry,
                target_as_source=not first,
            )
            first = False

        ctt_label = create_ctt_label(
            processing_rules=[
                self._name,
            ],
        )
        replication_resource_element.target.set_label(
            label=ctt_label,
        )

        if manifest_blob_ref := oci_client.head_manifest(
            image_reference=str(replication_resource_element.tgt_ref),
            absent_ok=True,
            accept=replication_mode.accept_header(),
        ):
            replication_resource_element.digest = manifest_blob_ref.digest

        return replication_resource_element


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


def _target(target_cfg: dict):
    target_type = target_cfg['type']
    target_ctor = getattr(targets, target_type, None)
    if not target_ctor:
        ci.util.fail(f'no such target: {target_type}')
    target = target_ctor(**target_cfg.get('kwargs', {}))
    return target


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
    shared_targets: dict={},
    shared_processors: dict={},
    shared_uploaders: dict={},
) -> ProcessingPipeline:
    name = processing_cfg.get('name', '<no name>')

    target_cfgs = processing_cfg['target']
    if not isinstance(target_cfgs, list):
        target_cfgs = [target_cfgs]

    def instantiate_target(target_cfg):
        if isinstance(target_cfg, str):
            return shared_targets[target_cfg]
        return _target(target_cfg)

    targets = [instantiate_target(target_cfg) for target_cfg in target_cfgs]

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
        targets=targets,
        filters=filters,
        processor=proc,
        uploaders=uploaders,
    )
    return pipeline


def enum_processing_cfgs(
    processing_cfg: dict,
    shared_targets: dict,
    shared_processors: dict,
    shared_uploaders: dict,
):
    cfg_entries = processing_cfg['image_processing_cfg']

    yield from map(
        processing_pipeline,
        cfg_entries,
        itertools.repeat(shared_targets, len(cfg_entries)),
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
        extra_crefs_label := component.find_label(ocm.gardener.ExtraComponentReferencesLabel.name)
    ):
        for extra_cref_raw in extra_crefs_label.value:
            extra_cref = dacite.from_dict(
                data_class=ocm.gardener.ExtraComponentReference,
                data=extra_cref_raw,
            )
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


uploaded_image_refs_to_digests = {}  # <ref>:<digest>
uploaded_image_refs_to_ready_events = {}  # <ref>:<event> (set if digest is available)
upload_image_lock = threading.Lock()


# uploads a single OCI artifact and returns the content digest
def process_upload_request(
    replication_resource_element: ctt.model.ReplicationResourceElement,
    oci_client: oci.client.Client,
    replication_mode: oci.ReplicationMode=oci.ReplicationMode.PREFER_MULTIARCH,
    platform_filter: collections.abc.Callable[[om.OciPlatform], bool]=None,
    inject_ocm_coordinates_into_oci_manifests: bool=False,
    processing_mode: ProcessingMode=ProcessingMode.REGULAR,
) -> str:
    src_ref = replication_resource_element.src_ref
    tgt_ref = replication_resource_element.tgt_ref

    if replication_resource_element.digest:
        logger.debug(f'{tgt_ref=} exists - skipping upload')
        return replication_resource_element.digest

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

    remove_files = replication_resource_element.remove_files
    component = replication_resource_element.component_id
    resource = replication_resource_element.target

    logger.info(
        f'processing {src_ref=} -> {tgt_ref=} {remove_files=} {replication_mode=} {platform_filter=}'
    )

    if inject_ocm_coordinates_into_oci_manifests:
        oci_manifest_annotations = {
            'cloud.gardener/ocm-component': f'{component.name}:{component.version}',
            'cloud.gardener/ocm-resource': f'{resource.name}:{resource.version}',
        }
    else:
        oci_manifest_annotations = None

    logger.debug(f'{oci_manifest_annotations=}')

    if processing_mode is ProcessingMode.DRY_RUN:
        manifest_digest = '<dummy-digest>'
        uploaded_image_refs_to_digests[tgt_ref] = f'sha256:{manifest_digest}'
        upload_done_event.set()
        return f'sha256:{manifest_digest}'

    try:
        _, patched_tgt_ref, raw_manifest = container.util.filter_image(
            source_ref=src_ref,
            target_ref=tgt_ref,
            remove_files=remove_files,
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
        logger.info(f'finished processing {src_ref=} -> {patched_tgt_ref=} (initial {tgt_ref=})')
    else:
        logger.info(f'finished processing {src_ref=} -> {tgt_ref=}')

    manifest_digest = hashlib.sha256(raw_manifest).hexdigest()
    uploaded_image_refs_to_digests[tgt_ref] = f'sha256:{manifest_digest}'
    upload_done_event.set()
    return f'sha256:{manifest_digest}'


def create_backwards_compatible_cfg(
    processing_cfg: dict,
    tgt_oci_registry: str,
    target_name: str='default',
) -> dict:
    # create missing `targets` configuration from explicitly passed function argument
    processing_cfg['targets'] = {
        target_name: {
            'type': 'RegistriesTarget',
            'kwargs': {
                'registries': [tgt_oci_registry],
            },
        },
    }

    # migrate `PrefixUploader` to `RepositoryUploader`
    migrated_uploaders = {}
    for uploader_name, uploader_cfg in processing_cfg['uploaders'].items():
        if uploader_cfg['type'] != 'PrefixUploader':
            # nothing to do
            migrated_uploaders[uploader_name] = uploader_cfg
            continue

        uploader_cfg['type'] = 'RepositoryUploader'

        _, tgt_repository = uploader_cfg['kwargs']['prefix'].rsplit('/', 1)
        uploader_cfg['kwargs']['repository'] = tgt_repository
        del uploader_cfg['kwargs']['prefix']

        migrated_uploaders[uploader_name] = uploader_cfg

    processing_cfg['uploaders'] = migrated_uploaders

    # patch-in target to image-processing-cfgs
    migrated_image_processing_cfg = []
    for image_processing_cfg in processing_cfg['image_processing_cfg']:
        image_processing_cfg['target'] = target_name
        migrated_image_processing_cfg.append(image_processing_cfg)

    processing_cfg['image_processing_cfg'] = migrated_image_processing_cfg

    return processing_cfg


def iter_replication_plan_components(
    component_descriptors: collections.abc.Iterable[ocm.ComponentDescriptor],
    tgt_ocm_repo: ocm.OciOcmRepository,
    remove_label: collections.abc.Callable[[str], bool] | None=None,
) -> collections.abc.Iterable[ctt.model.ReplicationComponentElement]:
    for component_descriptor in component_descriptors:
        # create copy to not unintentionally modify mutually/afterwards (e.g. relevant for in-memory
        # lookup)
        source = copy.deepcopy(component_descriptor)
        target = copy.deepcopy(component_descriptor)

        if target.component.current_ocm_repo.oci_ref != tgt_ocm_repo.oci_ref:
            target.component.repositoryContexts.append(tgt_ocm_repo)

        if remove_label:
            target.component.labels = [
                label for label in target.component.labels
                if not remove_label(label.name)
            ]

        yield ctt.model.ReplicationComponentElement(
            source=source,
            target=target,
        )


def iter_replication_resource_elements(
    component_descriptors: collections.abc.Iterable[ocm.ComponentDescriptor],
    processing_cfg: dict,
    tgt_oci_registry: str,
    oci_client: oci.client.Client,
    replication_mode: oci.ReplicationMode=oci.ReplicationMode.PREFER_MULTIARCH,
) -> collections.abc.Iterable[ctt.model.ReplicationResourceElement]:
    shared_targets = {
        name: _target(cfg) for name, cfg in processing_cfg.get('targets', {}).items()
    }
    shared_processors = {
        name: _processor(cfg) for name, cfg in processing_cfg.get('processors', {}).items()
    }
    shared_uploaders = {
        name: _uploader(cfg) for name, cfg in processing_cfg.get('uploaders', {}).items()
    }

    def create_replication_resource_element(
        component: ocm.Component,
        resource: ocm.Resource,
    ) -> ctt.model.ReplicationResourceElement | None:
        for pipeline in enum_processing_cfgs(
            processing_cfg=processing_cfg,
            shared_targets=shared_targets,
            shared_processors=shared_processors,
            shared_uploaders=shared_uploaders,
        ):
            replication_resource_element = pipeline.process(
                component=component,
                resource=resource,
                tgt_oci_registry=tgt_oci_registry,
                oci_client=oci_client,
                replication_mode=replication_mode,
            )

            if replication_resource_element:
                return replication_resource_element

        logger.debug(
            f'skipped processing: {component.name}:{resource.access} ({tgt_oci_registry=})'
        )

    components_with_resource = [
        (component_descriptor.component, resource)
        for component_descriptor in component_descriptors
        for resource in component_descriptor.component.resources
        if resource.access.type is ocm.AccessType.OCI_REGISTRY
    ]

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=16)
    yield from (
        replication_resource_element for replication_resource_element in executor.map(
            create_replication_resource_element,
            [component for component, _ in components_with_resource],
            [resource for _, resource in components_with_resource],
        ) if replication_resource_element
    )


def create_replication_plan_step(
    processing_cfg: dict,
    root_component_descriptor: ocm.ComponentDescriptor,
    src_component_descriptor_lookup: cnudie.retrieve.ComponentDescriptorLookupById,
    tgt_component_descriptor_lookup: cnudie.retrieve.ComponentDescriptorLookupById,
    tgt_oci_registry: str,
    tgt_ocm_repo_path: str,
    oci_client: oci.client.Client,
    replication_mode: oci.ReplicationMode=oci.ReplicationMode.PREFER_MULTIARCH,
    component_filter: collections.abc.Callable[[ocm.Component], bool] | None=None,
    reftype_filter: collections.abc.Callable[[cnudie.iter.NodeReferenceType], bool] | None=None,
    remove_label: collections.abc.Callable[[str], bool]=None,
) -> ctt.model.ReplicationPlanStep:
    tgt_ocm_repo = ocm.OciOcmRepository(
        baseUrl=ci.util.urljoin(tgt_oci_registry, tgt_ocm_repo_path),
    )

    component_descriptors = tuple(determine_changed_components(
        component_descriptor=root_component_descriptor,
        tgt_ocm_repo_url=tgt_ocm_repo.oci_ref,
        component_descriptor_lookup=src_component_descriptor_lookup,
        tgt_component_descriptor_lookup=tgt_component_descriptor_lookup,
        component_filter=component_filter,
        reftype_filter=reftype_filter,
    ))

    components = tuple(iter_replication_plan_components(
        component_descriptors=component_descriptors,
        tgt_ocm_repo=tgt_ocm_repo,
        remove_label=remove_label,
    ))

    resources = tuple(iter_replication_resource_elements(
        component_descriptors=component_descriptors,
        processing_cfg=processing_cfg,
        tgt_oci_registry=tgt_oci_registry,
        oci_client=oci_client,
        replication_mode=replication_mode,
    ))

    return ctt.model.ReplicationPlanStep(
        target_ocm_repository=tgt_ocm_repo.oci_ref,
        resources=resources,
        components=components,
    )


def process_images(
    processing_cfg_path: str,
    root_component_descriptor: ocm.ComponentDescriptor,
    component_descriptor_lookup: cnudie.retrieve.ComponentDescriptorLookupById,
    oci_client: oci.client.Client,
    processing_mode: ProcessingMode=ProcessingMode.REGULAR,
    replication_mode: oci.ReplicationMode=oci.ReplicationMode.PREFER_MULTIARCH,
    inject_ocm_coordinates_into_oci_manifests: bool=False,
    skip_cd_validation: bool=False,
    platform_filter: collections.abc.Callable[[om.OciPlatform], bool]=None,
    skip_component_upload: collections.abc.Callable[[ocm.Component], bool]=None,
    delivery_service_client: typing.Union['delivery.client.DeliveryServiceClient', None]=None,
    component_filter: collections.abc.Callable[[ocm.Component], bool]=None,
    remove_label: collections.abc.Callable[[str], bool]=None,
    tgt_ocm_repo_path: str=None,
    tgt_ocm_base_url: str | None=None, # deprecated -> replaced by `tgt_ocm_repo_path`
) -> collections.abc.Generator[cnudie.iter.Node, None, None]:
    '''
    note: Passing a filter to prevent component descriptors from being replicated using the
    `skip_component_upload` parameter will still replicate all its resources (i.e. oci images)
    as well as referenced components. In contrast to that, passing a filter using the
    `component_filter` parameter will also exclude its resources as well as all transitive component
    references from the replication. In both cases, `True` means the respective component is
    _excluded_.
    '''
    processing_cfg = parse_processing_cfg(processing_cfg_path)

    if tgt_ocm_base_url:
        tgt_oci_registry, tgt_ocm_repo_path = tgt_ocm_base_url.rsplit('/', 1)
        processing_cfg = create_backwards_compatible_cfg(
            processing_cfg=processing_cfg,
            tgt_oci_registry=tgt_oci_registry,
        )

    if not tgt_ocm_repo_path:
        raise ValueError(tgt_ocm_repo_path)

    reftype_filter = None
    if remove_label and remove_label(ocm.gardener.ExtraComponentReferencesLabel.name):
        def filter_extra_component_refs(reftype: cnudie.iter.NodeReferenceType) -> bool:
            return reftype is cnudie.iter.NodeReferenceType.EXTRA_COMPONENT_REFS_LABEL

        reftype_filter = filter_extra_component_refs

    if processing_mode is ProcessingMode.DRY_RUN:
        logger.warning('dry-run: not downloading or uploading any images')

    # all component descriptors are replicated to all target registries, but OCI artefacts are
    # only replicated to the respectively configured targets
    tgt_oci_registries = set()
    for target_cfg in processing_cfg['targets'].values():
        if 'registry' in target_cfg['kwargs']:
            tgt_oci_registries.add(target_cfg['kwargs']['registry'])
        elif 'registries' in target_cfg['kwargs']:
            tgt_oci_registries.update(target_cfg['kwargs']['registries'])

    replication_plan = ctt.model.ReplicationPlan()

    for tgt_oci_registry in tgt_oci_registries:
        tgt_component_descriptor_lookup = create_component_descriptor_lookup_for_ocm_repo(
            ocm_repo_url=ci.util.urljoin(tgt_oci_registry, tgt_ocm_repo_path),
            oci_client=oci_client,
            delivery_service_client=delivery_service_client,
        )

        replication_plan_step = create_replication_plan_step(
            processing_cfg=processing_cfg,
            root_component_descriptor=root_component_descriptor,
            src_component_descriptor_lookup=component_descriptor_lookup,
            tgt_component_descriptor_lookup=tgt_component_descriptor_lookup,
            tgt_oci_registry=tgt_oci_registry,
            tgt_ocm_repo_path=tgt_ocm_repo_path,
            oci_client=oci_client,
            replication_mode=replication_mode,
            component_filter=component_filter,
            reftype_filter=reftype_filter,
            remove_label=remove_label,
        )
        replication_plan.steps.append(replication_plan_step)

    logger.info(replication_plan)

    for replication_plan_step in replication_plan.steps:
        tgt_component_descriptor_lookup = create_component_descriptor_lookup_for_ocm_repo(
            ocm_repo_url=replication_plan_step.target_ocm_repository,
            oci_client=oci_client,
            delivery_service_client=delivery_service_client,
        )

        yield from process_replication_plan_step(
            replication_plan_step=replication_plan_step,
            root_component_descriptor=root_component_descriptor,
            oci_client=oci_client,
            tgt_component_descriptor_lookup=tgt_component_descriptor_lookup,
            processing_mode=processing_mode,
            replication_mode=replication_mode,
            inject_ocm_coordinates_into_oci_manifests=inject_ocm_coordinates_into_oci_manifests,
            platform_filter=platform_filter,
            component_filter=component_filter,
            reftype_filter=reftype_filter,
            skip_cd_validation=skip_cd_validation,
            skip_component_upload=skip_component_upload,
        )


def process_replication_plan_step(
    replication_plan_step: ctt.model.ReplicationPlanStep,
    root_component_descriptor: ocm.ComponentDescriptor,
    oci_client: oci.client.Client,
    tgt_component_descriptor_lookup: cnudie.retrieve.ComponentDescriptorLookupById,
    processing_mode: ProcessingMode=ProcessingMode.REGULAR,
    replication_mode: oci.ReplicationMode=oci.ReplicationMode.PREFER_MULTIARCH,
    inject_ocm_coordinates_into_oci_manifests: bool=False,
    platform_filter: collections.abc.Callable[[om.OciPlatform], bool]=None,
    component_filter: collections.abc.Callable[[ocm.Component], bool] | None=None,
    reftype_filter: collections.abc.Callable[[cnudie.iter.NodeReferenceType], bool] | None=None,
    skip_cd_validation: bool=False,
    skip_component_upload: collections.abc.Callable[[ocm.Component], bool] | None=None,
) -> collections.abc.Generator[cnudie.iter.Node, None, None]:
    def process_replication_resource_element(
        replication_resource_element: ctt.model.ReplicationResourceElement,
    ) -> ctt.model.ReplicationResourceElement:
        oci_manifest_digest = process_upload_request(
            replication_resource_element=replication_resource_element,
            oci_client=oci_client,
            replication_mode=replication_mode,
            platform_filter=platform_filter,
            inject_ocm_coordinates_into_oci_manifests=inject_ocm_coordinates_into_oci_manifests,
            processing_mode=processing_mode,
        )

        if not oci_manifest_digest:
            raise RuntimeError(f'No digest returned for {replication_resource_element=}')

        if (
            processing_mode is not ProcessingMode.DRY_RUN
            and (extra_tags := replication_resource_element.extra_tags)
        ):
            target_repo = replication_resource_element.tgt_ref.ref_without_tag
            manifest_bytes = oci_client.manifest_raw(
                image_reference=f'{target_repo}@{oci_manifest_digest}',
                accept=replication_mode.accept_header(),
            ).content

            for extra_tag in extra_tags:
                push_target = f'{target_repo}:{extra_tag}'

                oci_client.put_manifest(
                    image_reference=push_target,
                    manifest=manifest_bytes,
                )

        if digest := replication_resource_element.target.digest:
            # if resource has a digest we understand, and is an ociArtifact, then we need to
            # update the digest, because we might have changed the oci-artefact
            if (
                digest.hashAlgorithm.upper() == 'SHA-256'
                and digest.normalisationAlgorithm == ocm.NormalisationAlgorithm.OCI_ARTIFACT_DIGEST
            ):
                digest.value = oci_manifest_digest.removeprefix('sha256:')
                replication_resource_element.target.digest = digest

        if replication_resource_element.convert_to_relative_ref:
            # remove host from target ref
            replication_resource_element.target.access = ocm.RelativeOciAccess(
                reference=om.OciImageReference(
                    image_reference=replication_resource_element.tgt_ref,
                    normalise=False, # don't inject docker special handlings
                ).local_ref,
            )

        if not replication_resource_element.reference_by_digest:
            return replication_resource_element

        tgt_ref = replication_resource_element.tgt_ref

        if (
            replication_resource_element.retain_symbolic_tag
            and (tgt_ref.has_symbolical_tag or tgt_ref.has_mixed_tag)
        ):
            tgt_ref = f'{tgt_ref.with_symbolical_tag}@{oci_manifest_digest}'
        else:
            tgt_ref = f'{tgt_ref.ref_without_tag}@{oci_manifest_digest}'

        access_type = replication_resource_element.target.access.type
        if access_type is ocm.AccessType.OCI_REGISTRY:
            replication_resource_element.target.access.imageReference = tgt_ref
        elif access_type is ocm.AccessType.RELATIVE_OCI_REFERENCE:
            replication_resource_element.target.access.reference = tgt_ref
        else:
            raise ValueError(access_type)

        return replication_resource_element

    def wrap_process_resource(replication_resource_element: ctt.model.ReplicationResourceElement):
        try:
            return process_replication_resource_element(replication_resource_element)
        except Exception as e:
            logger.error(f'exception while processing {replication_resource_element=}')
            raise e

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=16)
    replication_resource_elements = tuple(executor.map(
        wrap_process_resource,
        replication_plan_step.resources,
    ))

    is_root_component_descriptor = lambda component_descriptor: (
        component_descriptor.component.name == root_component_descriptor.component.name
        and component_descriptor.component.version == root_component_descriptor.component.version
    )

    for replication_plan_component in replication_plan_step.components:
        if is_root_component_descriptor(replication_plan_component.target):
            # store modified root target component descriptor because `cnudie.iter.iter` won't
            # resolve the (updated) root component descriptor again
            root_component_descriptor = replication_plan_component.target

        component = replication_plan_component.target.component

        resource_group = [
            replication_resource_element.target
            for replication_resource_element in replication_resource_elements
            if replication_resource_element.component_id == component.identity()
        ]

        patched_resources = {}

        # patch-in overwrites (caveat: must be done sequentially, as lists are not threadsafe)
        # use copy of resources as peers to shortcut "self"-check in identity function
        peer_resources = [
            copy.deepcopy(resource)
            for resource in component.resources
        ]

        for resource in resource_group:
            patched_resources[resource.identity(peer_resources)] = resource

        component.resources = [
            patched_resources.get(resource.identity(peer_resources), resource)
            for resource in component.resources
        ]

        # Validate the patched component-descriptor and exit on fail
        if not skip_cd_validation:
            # ensure component-descriptor is json-serialisable
            raw = dataclasses.asdict(replication_plan_component.target)
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
            patched_component_descriptor=replication_plan_component.target,
            src_ocm_repo=orig_ocm_repo,
            oci_client=oci_client,
        )

    if processing_mode is ProcessingMode.DRY_RUN:
        return # early exit because components cannot be retrieved from target

    # retrieve component descriptor from the target registry as local descriptor might not contain
    # patched image references (if it was already existing the the target registry and thus patching
    # has been skipped)
    if not skip_component_upload or not skip_component_upload(root_component_descriptor.component):
        root_component_descriptor = tgt_component_descriptor_lookup(ocm.ComponentIdentity(
            name=root_component_descriptor.component.name,
            version=root_component_descriptor.component.version,
        ))

    for node in cnudie.iter.iter(
        component=root_component_descriptor,
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
