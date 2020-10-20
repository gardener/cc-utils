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
import processing.filters as p_filters
import processing.processing_component as pc
import processing.processing_model as processing_model
import processing.processors as p_processors
import processing.uploaders as p_uploaders
import processing.downloaders as p_downloaders

LOGGER = logging.getLogger(__name__)


class Action(enum.Enum):
    ARCHIVE = 'archive'
    CREATE = 'create'
    DOWNLOAD = 'download'
    EXTRACT = 'extract'
    UPLOAD = 'upload'
    SYNC = 'sync'


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
            map(
                lambda filtr, component, container_image: filtr.matches(component, container_image),
                self._filters,
                itertools.repeat(component, filters_count),
                itertools.repeat(container_image, filters_count),
            )
        )

    def process(self, component, container_image):
        if not self.matches(component, container_image):
            return None

        LOGGER.info(
            f'{self._name} will process image: '
            f'{component.name}:{container_image.access.imageReference}'
        )

        # This path will be used as download first then source
        image_tar_path = os.path.join(
            config.RESOURCES_DIR,
            ci.util.file_extension_join(
                container_image.access.imageReference,
                pc.FileExtension.TAR.value,
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
    filter_ctor = getattr(p_filters, filter_cfg['type'])
    filter_ = filter_ctor(**filter_cfg.get('kwargs', {}))

    return filter_


def _processor(processor_cfg: dict):
    proc_type = processor_cfg['type']
    proc_ctor = getattr(p_processors, proc_type, None)
    if not proc_ctor:
        ci.util.fail(f'no such image processor: {proc_type}')
    processor = proc_ctor(**processor_cfg.get('kwargs', {}))
    return processor


def _uploader(uploader_cfg: dict):
    upload_type = uploader_cfg['type']
    upload_ctor = getattr(p_uploaders, upload_type, None)
    if not upload_ctor:
        ci.util.fail(f'no such uploader: {upload_type}')
    uploader = upload_ctor(**uploader_cfg.get('kwargs', {}))
    return uploader


def processing_pipeline(
        processing_cfg: dict,
        shared_processors: dict,
        shared_uploaders: dict,
):
    name = processing_cfg.get('name', '<no name>')

    filter_cfgs = processing_cfg['filter']
    if isinstance(filter_cfgs, dict):
        filter_cfgs = [filter_cfgs]
    filters = [_filter(filter_cfg=filter_cfg) for filter_cfg in filter_cfgs]

    downloader = p_downloaders.Downloader()

    if 'processor' in processing_cfg:
        processor_cfg = processing_cfg['processor']
        if isinstance(processor_cfg, str):
            proc = shared_processors[processor_cfg]
        else:
            proc = _processor(processor_cfg=processor_cfg)
    else:
        proc = p_processors.NoOpProcessor()

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
                f'found matching processor: {component.name}: '
                f'{container_image.access.imageReference}'
            )
            yield job
            break
        else:
            ci.util.warning(
                f'no matching processor: {component.name}: '
                f'{container_image.access.imageReference}'
            )


def _enumerate_oci_resources(descriptor):
    for resource in itertools.chain(
            descriptor.component.externalResources,
            descriptor.component.localResources,
        ):
        if resource.access.type == cm.AccessType.OCI_REGISTRY and \
           resource.type == cm.ResourceType.OCI_IMAGE:
            yield (descriptor.component, resource)


class ProcessComponent:
    def __init__(self, processing_cfg, component_obj):
        self.src_component_obj = component_obj
        self.src_descriptor = component_obj.descriptor
        self.src_external_resources = self.src_descriptor.component.externalResources
        self.src_local_resources = self.src_descriptor.component.localResources

        self.tgt_external_resources = ProcessComponent.new_processing_resources(
            src_resources=self.src_external_resources
        )
        self.tgt_local_resources = ProcessComponent.new_processing_resources(
            src_resources=self.src_local_resources
        )

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=8)

        jobs = create_jobs(
            processing_cfg=processing_cfg,
            component_descriptor=self.src_descriptor,
        )

        for _ in executor.map(self.process_job, jobs):
            pass # force execution

    @staticmethod
    def new_processing_resources(
            src_resources: cm.Resource
    ) -> processing_model.ProcessingResources:
        '''
        Initiate a ProcessingResources with:
            resources: only the non OCI_REGISTRY resources are added from the source
                component descritor, the target modified resources will be added
                incrementally as they are processed.
            expected_count: this is based on the total count of resources from the source
                component descriptor.
        '''
        return processing_model.ProcessingResources(
            resources=[r for r in src_resources if r.access.type != cm.AccessType.OCI_REGISTRY],
            expected_count=len(src_resources)
        )

    def all_tgt_resources_processed(self):
        """ check if the number of processed resources has reached the expected count """
        return all(
            [
                (len(r.resources) == r.expected_count) for r in [
                    self.tgt_external_resources,
                    self.tgt_local_resources
                ]
            ]
        )

    def tgt_descriptor_upload(self, tgt_context_url):
        tgt_component_obj = pc.ComponentTool.new_from_source_descriptor(
            descriptor=self.src_descriptor,
            context_url=tgt_context_url,
            external_resources=self.tgt_external_resources.resources,
            local_resources=self.tgt_local_resources.resources,
        )

        tgt_component_obj.write_descriptor_to_file()
        product.v2.upload_component_descriptor_v2_to_oci_registry(tgt_component_obj.descriptor)

    def process_job(self, processing_job):
        src_img = processing_job.container_image
        tgt_oci_ref = processing_job.upload_request.target_ref
        tgt_img = pc.new_oci_resource_image_ref(
            resource=src_img,
            oci_ref=tgt_oci_ref
        )

        # find if target image resource is external or local
        if src_img in self.src_external_resources:
            self.tgt_external_resources.resources.append(tgt_img)
        if src_img in self.src_local_resources:
            self.tgt_local_resources.resources.append(tgt_img)

        # do actual processing
        if not config.DRY_RUN:
            if Action.DOWNLOAD.value in config.ACTIONS:
                self.src_component_obj.write_descriptor_to_file()
                container.util.process_download_request(processing_job.download_request)

            if Action.UPLOAD.value in config.ACTIONS:
                if not os.path.isfile(processing_job.upload_request.source_file):
                    ci.util.error(f'local tar image does not exist: '
                                  f'{processing_job.upload_request.source_file}')
                    return

                container.util.process_upload_request_from_file(
                    request=processing_job.upload_request
                )

                # All images have been processed, create and upload the new descriptor
                if self.all_tgt_resources_processed():
                    self.tgt_descriptor_upload(
                        tgt_context_url=processing_job.upload_context_url,
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
