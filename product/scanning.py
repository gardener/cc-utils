# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
# under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from enum import Enum
from functools import partial
import tempfile

from protecode.client import ProtecodeApi
from protecode.model import (
    ProcessingStatus,
    AnalysisResult,
    TriageScope,
)
from util import not_none, warning, check_type, info, urljoin
from container.registry import retrieve_container_image, publish_container_image
from .model import ContainerImage, Component, UploadResult, UploadStatus


class ProcessingMode(Enum):
    UPLOAD_IF_CHANGED = 'upload_if_changed'
    RESCAN = 'rescan'
    FORCE_UPLOAD = 'force_upload'


class UploadAction(Enum):
    def __init__(self, upload, rescan):
        self.upload = upload
        self.rescan = rescan

    SKIP = (False, False)
    UPLOAD = (True, False)
    RESCAN = (False, True)


class ProtecodeUtil(object):
    def __init__(
            self,
            protecode_api: ProtecodeApi,
            processing_mode: ProcessingMode=ProcessingMode.UPLOAD_IF_CHANGED,
            group_id: int=None,
            upload_registry_prefix: str=None,
            reference_group_ids=(),
    ):
        protecode_api.login()
        self._processing_mode = check_type(processing_mode, ProcessingMode)
        self._api = not_none(protecode_api)
        self._group_id = group_id
        self._upload_registry_prefix = upload_registry_prefix
        self._reference_group_ids = reference_group_ids

    def _image_ref_metadata(self, container_image, omit_version):
        metadata_dict = {
            'IMAGE_REFERENCE_NAME': container_image.name(),
        }
        if not omit_version:
            metadata_dict['IMAGE_REFERENCE'] = container_image.image_reference()

        return metadata_dict

    def _component_metadata(self, component, omit_version=True):
        metadata = {'COMPONENT_NAME': component.name()}
        if not omit_version:
            metadata['COMPONENT_VERSION'] = component.version()

        return metadata

    def _upload_name(self, container_image, component):
        image_reference = container_image.image_reference()
        image_path, image_tag = image_reference.split(':')
        image_name = image_path.split('/')[-1]
        return '{c}_{i}_{v}'.format(
            c=component.name(),
            i=image_name,
            v=image_tag,
        )

    def _update_product_name(self, product_id: int, upload_name: str):
        scan_result = self._api.scan_result_short(product_id=product_id)
        current_name = scan_result.name()

        if current_name == upload_name:
            return # nothing to do

        self._api.set_product_name(product_id=product_id, name=upload_name)

    def _metadata(
            self,
            container_image: ContainerImage,
            component: Component,
            omit_version,
        ):
        metadata = self._image_ref_metadata(container_image, omit_version=omit_version)
        metadata.update(self._component_metadata(component=component, omit_version=omit_version))
        return metadata

    def retrieve_scan_result(
            self,
            container_image: ContainerImage,
            component: Component,
            group_id: int=None,
        ):
        metadata = self._metadata(
            container_image=container_image,
            component=component,
            omit_version=True, # omit version when searching for existing app
            # (only one component version must exist per group by our chosen definition)
        )
        if not group_id:
            group_id = self._group_id

        existing_products = self._api.list_apps(
            group_id=group_id,
            custom_attribs=metadata
        )
        if len(existing_products) == 0:
            return None # no result existed yet

        if len(existing_products) > 1:
            warning('found more than one product for image {i}'.format(i=container_image))
            product_ids_to_rm = {p.product_id() for p in existing_products[1:]}
            for product_id in product_ids_to_rm:
                self._api.delete_product(product_id)
                info(f'deleted product with product_id: {product_id}')

        # use first (or only) match (we already printed a warning if we found more than one)
        product =  existing_products[0]
        product_id = product.product_id()

        # update upload name to reflect new component version (if changed)
        upload_name = self._upload_name(container_image, component)
        self._update_product_name(product_id, upload_name)

        # retrieve existing product's details (list of products contained only subset of data)
        product = self._api.scan_result(product_id=product_id)
        return product

    def _determine_upload_action(
            self,
            container_image: ContainerImage,
            scan_result: AnalysisResult,
    ):
        check_type(container_image, ContainerImage)

        if self._processing_mode in (
            ProcessingMode.UPLOAD_IF_CHANGED,
            ProcessingMode.RESCAN,
            ProcessingMode.FORCE_UPLOAD,
        ):
            # if no scan_result is available, we have to upload in all cases
            if not scan_result:
                return UploadAction.UPLOAD

        # determine if image to be uploaded is already present in protecode
        metadata = scan_result.custom_data()
        image_reference = metadata.get('IMAGE_REFERENCE')
        image_changed = image_reference != container_image.image_reference()

        if image_changed:
            return UploadAction.UPLOAD

        if self._processing_mode is ProcessingMode.UPLOAD_IF_CHANGED:
            return UploadAction.SKIP
        elif self._processing_mode is ProcessingMode.RESCAN:
            short_scan_result = self._api.scan_result_short(scan_result.product_id())

            if short_scan_result.is_stale():
                if not short_scan_result.has_binary():
                    return UploadAction.UPLOAD
                else:
                    return UploadAction.RESCAN
            else:
                return UploadAction.SKIP
        elif self._processing_mode is ProcessingMode.FORCE_UPLOAD:
            return UploadAction.UPLOAD
        else:
            raise NotImplementedError

    def upload_image(
            self,
            container_image: ContainerImage,
            component: Component,
        ) -> UploadResult:
        metadata = self._metadata(
            container_image=container_image,
            component=component,
            omit_version=False,
        )

        upload_result = partial(UploadResult, container_image=container_image, component=component)

        # check if the image has already been uploaded for this component
        scan_result = self.retrieve_scan_result(
            container_image=container_image,
            component=component,
        )
        reference_results = [
            self.retrieve_scan_result(
                container_image=container_image,
                component=component,
                group_id=group_id,
            ) for group_id in self._reference_group_ids
        ]
        reference_results = [r for r in reference_results if r] # remove None entries
        if scan_result:
            reference_results.insert(0, scan_result)

        # collect old triages in order to "transport" them after new upload (may be None)
        triages = self._existing_triages(
            analysis_results=reference_results,
        )

        upload_action = self._determine_upload_action(
            container_image=container_image,
            scan_result=scan_result
        )

        if not upload_action.upload and not upload_action.rescan:
            # early exit (nothing to do)
            return upload_result(
                status=UploadStatus.SKIPPED,
                result=scan_result,
            )

        if upload_action.upload:
            image_data_fh = retrieve_container_image(
                container_image.image_reference(),
                outfileobj=tempfile.NamedTemporaryFile(),
            )
            if self._upload_registry_prefix:
                self.upload_image_to_container_registry(container_image, image_data_fh)
            # keep old product_id (in order to delete after update)
            if scan_result:
                product_id = scan_result.product_id()
            else:
                product_id = None

            try:
                # Upload image and update outdated analysis result with the one triggered
                # by the upload.
                scan_result = self._api.upload(
                    application_name=self._upload_name(
                        container_image=container_image,
                        component=component
                    ).replace('/', '_'),
                    group_id=self._group_id,
                    data=image_data_fh,
                    custom_attribs=metadata,
                )
            finally:
                image_data_fh.close()

            # upload triaging results - hard-code scope for now
            for triage in triages:
                self._api.add_triage(
                    triage=triage,
                    scope=TriageScope.RESULT,
                    product_id=scan_result.product_id(),
                )

            # rm (now outdated) scan result
            if product_id:
                self._api.delete_product(product_id=product_id)

        if upload_action.rescan:
            self._api.rescan(scan_result.product_id())

        result = self._api.wait_for_scan_result(product_id=scan_result.product_id())

        if result.status() == ProcessingStatus.BUSY:
            # Should not happen since we waited until the scan result is ready.
            raise RuntimeError(
                'Analysis of container-image {c} was reported as completed, '
                'but is still pending'.format(
                    c=container_image.name(),
                )
            )
        else:
            upload_status = UploadStatus.DONE

        return upload_result(
            status=upload_status,
            result=result
        )

    def _existing_triages(self, analysis_results: AnalysisResult=()):
        if not analysis_results:
            return ()

        for analysis_result in analysis_results:
            for component in analysis_result.components():
                for vulnerability in component.vulnerabilities():
                    yield from vulnerability.triages()

    def upload_image_to_container_registry(self, container_image, image_data_fh):
        '''
        uploads the given container images (read from the passed fileobj, which
        must have a name) to the configured container registry.
        The original image reference is mangled, and prefixed with the configured
        prefix.
        '''
        original_reference = container_image.image_reference()
        mangled_reference = original_reference.replace('.', '_')
        target_reference = urljoin(self._upload_registry_prefix, mangled_reference)

        publish_container_image(
            image_reference=target_reference,
            image_file_obj=image_data_fh,
        )
