import collections
import concurrent.futures
import enum
import itertools
import logging
import os

import gci.componentmodel as cm

import ci.util
import container.model
import container.util
import product.v2

import processing.config as config
import processing.filters as filters
import processing.processing_component as pc
import processing.processing_model as processing_model
import processing.processors as processors
import processing.uploaders as uploaders
import processing.downloaders as downloaders

logger = logging.getLogger(__name__)


class Action(enum.Enum):
    ARCHIVE = 'archive'
    CREATE = 'create'
    DOWNLOAD = 'download'
    EXTRACT = 'extract'
    UPLOAD = 'upload'
    SYNC = 'sync'


class FileExtension(enum.Enum):
    COMPONENT_DESCRIPTOR = 'yaml'
    TAR = 'tar'


class ProcessingPipeline:
    def __init__(
        self,
        name,
        filters,
        downloader,
        processor,
        uploaders,
    ):
        self._name = name
        self._filters = filters
        self._downloader = downloader
        self._processor = processor
        self._uploaders = uploaders

    def matches(self, component, container_image):
        filters_count = len(self._filters)
        return all(
            map(lambda filtr, component, container_image: filtr.matches(component, container_image),
                self._filters,
                itertools.repeat(component, filters_count),
                itertools.repeat(container_image, filters_count),
            )
        )

    def process(self, component, container_image):
        if not self.matches(component, container_image):
            return None

        logger.info(
            f'{self._name} will process image: '
            f'{component.name}:{container_image.access.imageReference}'
        )

        # This path will be used as download first then source
        image_tar_path = os.path.join(
            config.RESOURCES_DIR,
            ci.util.file_extension_join(
                container_image.access.imageReference,
                FileExtension.TAR.value,
            )
        )

        job = processing_model.ProcessingJob(
            component=component,
            container_image=container_image,
            download_request=None, # will be set by process based on AccessType
            upload_request=container.model.ContainerImageUploadRequest(
                source_ref=container_image.access.imageReference,
                source_file=image_tar_path,
                target_ref=None, # must be set by a later step
                processing_callback=None, # _may_ be set by a later step
            ),
            upload_context_url=None, # set by uploader
        )

        job = self._downloader.process(
            processing_job=job,
            target_file=image_tar_path
        )
        job = self._processor.process(processing_job=job)

        first = True
        for uploader in self._uploaders:
            job = uploader.process(job, target_as_source=not first)
            first = False

        return job


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
):
    name = processing_cfg.get('name', '<no name>')

    filter_cfgs = processing_cfg['filter']
    if isinstance(filter_cfgs, dict):
        filter_cfgs = [filter_cfgs]
    filters = [_filter(filter_cfg=filter_cfg) for filter_cfg in filter_cfgs]

    downloader = downloaders.Downloader()

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
        upload_cfgs = [upload_cfgs] # normalise to list

    def instantiate_uploader(upload_cfg):
        if isinstance(upload_cfg, str):
            return shared_uploaders[upload_cfg]
        return _uploader(upload_cfg)

    uploaders = [instantiate_uploader(upload_cfg) for upload_cfg in upload_cfgs]

    pipeline = ProcessingPipeline(
        name=name,
        filters=filters,
        downloader=downloader,
        processor=proc,
        uploaders=uploaders,
    )

    return pipeline


def enum_processing_cfgs(
    processing_cfg: dict,
    shared_processors: dict,
    shared_uploaders: dict,
):
    cfg_entries = processing_cfg['processing_cfg']

    yield from map(
        processing_pipeline,
        cfg_entries,
        itertools.repeat(shared_processors, len(cfg_entries)),
        itertools.repeat(shared_uploaders, len(cfg_entries)),
    )


def create_jobs(processing_cfg, component_descriptor):
    shared_processors = {
        name: _processor(cfg) for name, cfg in processing_cfg.get('processors', {}).items()
    }
    shared_uploaders = {
        name: _uploader(cfg) for name, cfg in processing_cfg.get('uploaders', {}).items()
    }

    for component, container_image in _enumerate_oci_resources(component_descriptor):
        for processor in enum_processing_cfgs(
            processing_cfg,
            shared_processors,
            shared_uploaders,
        ):

            job = processor.process(component=component, container_image=container_image)
            if not job:
                continue # processor did not want to process

            ci.util.info(
                f'found matching processor: {component.name}:{container_image.access.imageReference}'
            )
            yield job
            break
        else:
            ci.util.warning(
                f'no matching processor: {component.name}:{container_image.access.imageReference}'
            )


def _enumerate_oci_resources(descriptor):
    for resource in itertools.chain(
            descriptor.component.externalResources,
            descriptor.component.localResources,
        ):
        if resource.access.type == cm.AccessType.OCI_REGISTRY and \
           resource.type == cm.ResourceType.OCI_IMAGE:
            yield (descriptor.component, resource)


def process_resources(processing_cfg, component_obj):
    if config.DRY_RUN:
        ci.util.warning('dry-run: not downloading or uploading any images')

    src_descriptor = component_obj.descriptor
    src_component = src_descriptor.component

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=8)

    jobs = create_jobs(
        processing_cfg=processing_cfg,
        component_descriptor=src_descriptor,
    )

    NewResources = collections.namedtuple(
        'NewResources',
        ['resources', 'expected']
    )

    # only modify the OCI_REGISTRY resources
    def _new_resources(resources: cm.AccessType) -> NewResources:
        return NewResources(
            resources=list(r for r in resources if r.access.type != cm.AccessType.OCI_REGISTRY),
            expected=len(resources)
        )
    tgt_external_resources = _new_resources(src_component.externalResources)
    tgt_local_resources = _new_resources(src_component.localResources)

    def process_job(processing_job):
        src_img = processing_job.container_image
        tgt_oci_ref = processing_job.upload_request.target_ref
        tgt_img = pc.new_oci_resource_image_ref(
            resource=src_img,
            oci_ref=tgt_oci_ref
        )

        if src_img in src_component.externalResources:
            tgt_external_resources.resources.append(tgt_img)

        if src_img in src_component.localResources:
            tgt_local_resources.resources.append(tgt_img)

        def _tgt_resources_fully_processed(*resources):
            return (len(r) == e for r, e in resources)

        def _upload_tgt_component(src_descriptor, context_url, external_resources, local_resources):
            tgt_component_obj = pc.ComponentTool.new_from_source_descriptor(
                descriptor=src_descriptor,
                context_url=context_url,
                external_resources=external_resources.resources,
                local_resources=tgt_local_resources.resources,
            )

            tgt_component_obj.write_descriptor_to_file()
            product.v2.upload_component_descriptor_v2_to_oci_registry(tgt_component_obj.descriptor)

        # do actual processing
        if not config.DRY_RUN:
            if Action.DOWNLOAD.value in config.ACTIONS:
                component_obj.write_descriptor_to_file()
                container.util.process_download_request(processing_job.download_request)

            if Action.UPLOAD.value in config.ACTIONS:
                if not os.path.isfile(processing_job.upload_request.source_file):
                    ci.util.error(f'local tar image does not exist: '
                                  f'{processing_job.upload_request.source_file}')
                    return

                container.util.process_upload_request_from_file(
                        request=processing_job.upload_request
                )

                # All images have been processed, we can create the new component descriptor
                if all(_tgt_resources_fully_processed(
                    tgt_external_resources,
                    tgt_local_resources)
                ):
                    _upload_tgt_component(
                            src_descriptor=src_descriptor,
                            context_url=processing_job.upload_context_url,
                            external_resources=tgt_external_resources,
                            local_resources=tgt_local_resources,
                    )
        elif config.DRY_RUN:
            if Action.DOWNLOAD.value in config.ACTIONS:
                ci.util.info(f'download image {processing_job.download_request.source_ref} to '
                             f'{processing_job.download_request.target_file}')
            if Action.UPLOAD.value in config.ACTIONS:
                ci.util.info(f'upload {processing_job.upload_request.source_file} to '
                             f'{processing_job.upload_request.target_ref}')
                if not os.path.isfile(processing_job.upload_request.source_file):
                    ci.util.warning(f'local tar image does not exist: '
                                    f'{processing_job.upload_request.source_file}')

            pass

    for result in executor.map(process_job, jobs):
        pass # force execution
