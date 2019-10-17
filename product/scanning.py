# Copyright (c) 2019 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
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
from functools import partial
import enum
import semver
import tempfile
import typing

from protecode.client import ProtecodeApi
from protecode.model import (
    ProcessingStatus,
    AnalysisResult,
    TriageScope,
)
from concourse.model.base import (
    AttribSpecMixin,
    AttributeSpec,
)
from ci.util import not_none, warning, check_type, info
from container.registry import retrieve_container_image
from .model import ContainerImage, Component, UploadResult, UploadStatus
import ci.util


class ProcessingMode(AttribSpecMixin, enum.Enum):
    UPLOAD_IF_CHANGED = 'upload_if_changed'
    RESCAN = 'rescan'
    FORCE_UPLOAD = 'force_upload'

    @classmethod
    def _attribute_specs(cls):
        return (
            AttributeSpec.optional(
                name=cls.UPLOAD_IF_CHANGED.value, # XXX deprecated!! this is useless
                default=None,
                doc='''
                    upload absent container images. This will _not_ upload images already present.
                    Present images will _not_ be rescanned.
                ''',
                type=str,
            ),
            AttributeSpec.optional(
                name=cls.RESCAN.value,
                default=None,
                doc='''
                    (re-)scan container images if Protecode indicates this might bear new results.
                    Upload absent images.
                ''',
                type=str,
            ),
            AttributeSpec.optional(
                name=cls.FORCE_UPLOAD.value,
                default=None,
                doc='''
                    `always` upload and scan all images.
                ''',
                type=str,
            ),
        )


class UploadAction(enum.Enum):
    def __init__(self, upload, rescan, wait, transport_triages):
        self.upload = upload
        self.rescan = rescan
        self.wait = wait
        self.transport_triages = transport_triages

    SKIP = (False, False, False, True)
    UPLOAD = (True, False, True, True)
    RESCAN = (False, True, True, True)
    WAIT_FOR_RESULT = (False, False, True, False)


class ContainerImageGroup(object):
    '''
    A set of Container Images sharing a common declaring component and a common logical image name.

    Container Image Groups are intended to be handled as "virtual Protecode Groups".
    This particularly means they share triages.

    As a very common "special case", a container image group may contain exactly one container image.

    @param component: the Component declaring dependency towards the given images
    @param container_image: iterable of ContainerImages; must share logical name
    '''
    def __init__(
        self,
        component,
        container_images: typing.Iterable[ContainerImage],
    ):
        def _to_semver(container_image):
            # XXX do this centrally
            version_str = container_image.version()
            return semver.parse_version_info(version_str.lstrip('v'))

        self._component = component

        self._container_images = list(container_images)

        if not len(self._container_images) > 0:
            raise ValueError('at least one container image must be given')

        # workaround (not all versions are valid semver-versions unfortunately) :-(((
        # - so at least do not try to parse in case no sorting is required
        if len(container_images) > 1:
            # sort, smallest version first
            self._container_images = sorted(
                self._container_images,
                key=_to_semver,
            )

        image_name = {i.name() for i in self._container_images}
        if len(image_name) > 1:
            raise ValueError(f'all images must share same name: {image_name}')
        self._image_name = image_name.pop()

        # todo: also validate all images are in fact declared by given component

    def component(self):
        return self._component

    def image_name(self):
        return self._image_name

    def images(self):
        '''
        @returns sorted iterable containing all images (smallest version first)
        '''
        return self._container_images

    def __iter__(self):
        return self._container_images.__iter__()


class ProtecodeUtil(object):
    def __init__(
            self,
            protecode_api: ProtecodeApi,
            processing_mode: ProcessingMode=ProcessingMode.RESCAN,
            group_id: int=None,
            reference_group_ids=(),
    ):
        protecode_api.login()
        self._processing_mode = check_type(processing_mode, ProcessingMode)
        self._api = not_none(protecode_api)
        self._group_id = group_id
        self._reference_group_ids = reference_group_ids

    def _image_group_metadata(
        self,
        container_image_group: ContainerImageGroup,
        omit_version=False,
    ):
        metadata = {
            'IMAGE_REFERENCE_NAME': container_image_group.image_name(),
            'COMPONENT_NAME': container_image_group.component().name(),
        }

        if not omit_version:
            metadata['COMPONENT_VERSION'] = container_image_group.component().version()

        return metadata

    def _image_ref_metadata(self, container_image, omit_version):
        metadata_dict = {
            'IMAGE_REFERENCE_NAME': container_image.name(),
        }
        if not omit_version:
            metadata_dict['IMAGE_REFERENCE'] = container_image.image_reference()
            metadata_dict['IMAGE_VERSION'] = container_image.version()

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
        return '{i}_{v}_{c}'.format(
            i=image_name,
            v=image_tag,
            c=component.name(),
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

    def upload_container_image_group(
        self,
        container_image_group: ContainerImageGroup,
    ):
        # depending on upload-mode, determine an upload-action for each related image
        # - images to upload
        # - protecode-apps to remove
        # - triages to import
        images_to_upload = set()
        protecode_apps_to_remove = set()
        protecode_apps_to_consider = set() # consider to rescan; return results
        triages_to_import = set()

        metadata = self._image_group_metadata(
            container_image_group=container_image_group,
            omit_version=True,
        )

        existing_products = self._api.list_apps(
            group_id=self._group_id,
            custom_attribs=metadata,
        )

        # import triages from local group
        scan_results = (
            self._api.scan_result(product_id=product.product_id())
            for product in existing_products
        )
        triages_to_import |= set(self._existing_triages(scan_results))

        # import triages from reference groups
        def enumerate_reference_triages():
            for group_id in self._reference_group_ids:
                ref_apps = self._api.list_apps(
                    group_id=group_id,
                    custom_attribs=metadata,
                )
                ref_scan_results = (
                    self._api.scan_result(app.product_id())
                    for app in ref_apps
                )
                yield from self._existing_triages(ref_scan_results)

        triages_to_import |= set(enumerate_reference_triages())

        if self._processing_mode is ProcessingMode.FORCE_UPLOAD:
            ci.util.info(f'force-upload - will re-upload all images')
            images_to_upload |= set(container_image_group.images())
            # remove all
            protecode_apps_to_remove = set(existing_products)
        elif self._processing_mode in (ProcessingMode.RESCAN, ProcessingMode.UPLOAD_IF_CHANGED):
            for container_image in container_image_group.images():
                # find matching protecode product (aka app)
                for existing_product in existing_products:
                    if existing_product.custom_data().get('IMAGE_VERSION') == \
                      container_image.version():
                        existing_products.remove(existing_product)
                        protecode_apps_to_consider.add(existing_product)
                        break
                else:
                    ci.util.info(f'did not find image {container_image} - will upload')
                    # not found -> need to upload
                    images_to_upload.add(container_image)

            # all existing products that did not match shall be removed
            protecode_apps_to_remove |= set(existing_products)

        else:
            raise NotImplementedError()

        # trigger rescan if recommended
        for protecode_app in protecode_apps_to_consider:
            scan_result = self._api.scan_result_short(product_id=protecode_app.product_id())

            if not scan_result.is_stale():
                continue # protecode does not recommend a rescan

            if not scan_result.has_binary():
                image_version = scan_result.metadata().get('IMAGE_VERSION')
                # there should be at most one matching image (by version)
                for container_image in container_image_group:
                    if container_image.version() == image_version:
                        images_to_upload.add(container_image)
                        protecode_apps_to_consider.remove(protecode_app)
                        # xxx - also add app for removal?
                        break
            else:
                self._api.rescan(protecode_app.product_id())

        # upload new images
        for container_image in images_to_upload:
            scan_result = self._upload_image(
                component=container_image_group.component(),
                container_image=container_image,
            )
            protecode_apps_to_consider.add(scan_result)

        # wait for all apps currently being scanned
        for protecode_app in protecode_apps_to_consider:
            # replace - potentially incomplete - scan result
            protecode_apps_to_consider.remove(protecode_app)
            ci.util.info(f'waiting for {protecode_app.product_id()}')
            protecode_apps_to_consider.add(
                self._api.wait_for_scan_result(protecode_app.product_id())
            )

        # apply imported triages for all protecode apps
        for protecode_app in protecode_apps_to_consider:
            product_id = protecode_app.product_id()
            self._transport_triages(triages_to_import, product_id)

        # yield results
        for protecode_app in protecode_apps_to_consider:
            yield self._api.scan_result(protecode_app.product_id())

        # rm all outdated protecode apps
        for protecode_app in protecode_apps_to_remove:
            product_id = protecode_app.product_id()
            self._api.delete_product(product_id=product_id)

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
            products_to_rm = existing_products[1:]
            for p in products_to_rm:
                self._api.delete_product(p.product_id())
                info(
                    f'deleted product {p.display_name()} '
                    f'with product_id: {p.product_id()}'
                )

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

        # take shortcut if 'force upload' is configured.
        if self._processing_mode is ProcessingMode.FORCE_UPLOAD:
            return UploadAction.UPLOAD

        if self._processing_mode in (
            ProcessingMode.UPLOAD_IF_CHANGED,
            ProcessingMode.RESCAN,
        ):
            # if no scan_result is available, we have to upload in all remaining cases
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
            # Wait for the current scan to finish if it there is still one pending
            if scan_result.status() is ProcessingStatus.BUSY:
                return UploadAction.WAIT_FOR_RESULT
            short_scan_result = self._api.scan_result_short(scan_result.product_id())

            if short_scan_result.is_stale():
                if not short_scan_result.has_binary():
                    return UploadAction.UPLOAD
                else:
                    return UploadAction.RESCAN
            else:
                return UploadAction.SKIP
        else:
            raise NotImplementedError

    def upload_image(
            self,
            container_image: ContainerImage,
            component: Component,
        ) -> UploadResult:
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

        # Transport triages early. For the upload-case we need to transport triages only after
        # the image upload, so we do it later.
        if upload_action.transport_triages and not upload_action.upload:
            self._transport_triages(triages, scan_result.product_id())

        if not upload_action.upload and not upload_action.rescan and not upload_action.wait:
            # early exit (nothing to do)
            return upload_result(
                status=UploadStatus.SKIPPED,
                result=scan_result,
            )

        if upload_action.upload:
            info(f'uploading to protecode: {container_image.image_reference()}')
            # keep old product_id (in order to delete after update)
            if scan_result:
                product_id = scan_result.product_id()
            else:
                product_id = None

            scan_result = self._upload_image(
                component=component,
                container_image=container_image,
            )

            self._transport_triages(triages, scan_result.product_id())

            # rm (now outdated) scan result
            if product_id:
                self._api.delete_product(product_id=product_id)

        if upload_action.rescan:
            self._api.rescan(scan_result.product_id())

        if upload_action.wait:
            result = self._api.wait_for_scan_result(product_id=scan_result.product_id())
        else:
            result = scan_result

        if result.status() == ProcessingStatus.BUSY:
            upload_status = UploadStatus.PENDING
        else:
            upload_status = UploadStatus.DONE

        return upload_result(
            status=upload_status,
            result=result
        )

    def _upload_image(
        self,
        component: Component,
        container_image: ContainerImage,
    ):
        metadata = self._metadata(
            container_image=container_image,
            component=component,
            omit_version=False,
        )

        image_data_fh = retrieve_container_image(
            container_image.image_reference(),
            outfileobj=tempfile.NamedTemporaryFile(),
        )

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
            return scan_result
        finally:
            image_data_fh.close()

    def _transport_triages(self, triages, product_id):
        for triage in triages:
            if triage.scope() is TriageScope.GROUP:
                self._api.add_triage(
                    triage=triage,
                    scope=TriageScope.GROUP,
                    group_id=self._group_id,
                )
            else:
                # hard-code scope for now
                self._api.add_triage(
                    triage=triage,
                    scope=TriageScope.RESULT,
                    product_id=product_id,
                )

    def _existing_triages(self, analysis_results: typing.Iterable[AnalysisResult]=()):
        if not analysis_results:
            return ()

        for analysis_result in analysis_results:
            ci.util.check_type(analysis_result, AnalysisResult)
            for component in analysis_result.components():
                for vulnerability in component.vulnerabilities():
                    yield from vulnerability.triages()
