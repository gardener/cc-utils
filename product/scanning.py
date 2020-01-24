# Copyright (c) 2019-2020 SAP SE or an SAP affiliate company. All rights reserved. This file is
# licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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
import logging
import tempfile
import typing

import requests
import requests.exceptions

import ccc.grafeas
import protecode.model
from protecode.client import ProtecodeApi
from protecode.model import (
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
import version

ci.util.ctx().configure_default_logging()
logger = logging.getLogger(__name__)


class ProcessingMode(AttribSpecMixin, enum.Enum):
    RESCAN = 'rescan'
    FORCE_UPLOAD = 'force_upload'

    @classmethod
    def _attribute_specs(cls):
        return (
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
        self._component = component

        self._container_images = list(container_images)

        if not len(self._container_images) > 0:
            raise ValueError('at least one container image must be given')

        # do not try to parse in case no sorting is required
        if len(container_images) > 1:
            # sort, smallest version first
            self._container_images = sorted(
                self._container_images,
                key=version.parse_to_semver,
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
            cvss_threshold: int=7,
    ):
        protecode_api.login()
        self._processing_mode = check_type(processing_mode, ProcessingMode)
        self._api = not_none(protecode_api)
        self._group_id = group_id
        self._reference_group_ids = reference_group_ids
        self.cvss_threshold = cvss_threshold

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
        image_name = container_image.image_name()
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
    ) -> typing.Iterable[UploadResult]:
        mk_upload_result = partial(
            UploadResult,
            component=container_image_group.component(),
        )

        # depending on upload-mode, determine an upload-action for each related image
        # - images to upload
        # - protecode-apps to remove
        # - triages to import
        images_to_upload = set()
        protecode_apps_to_remove = set()
        protecode_apps_to_consider = list() # consider to rescan; return results
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
        ci.util.info(f'found {len(triages_to_import)} triage(s) to import')

        if self._processing_mode is ProcessingMode.FORCE_UPLOAD:
            ci.util.info('force-upload - will re-upload all images')
            images_to_upload |= set(container_image_group.images())
            # remove all
            protecode_apps_to_remove = set(existing_products)
        elif self._processing_mode is ProcessingMode.RESCAN:
            for container_image in container_image_group.images():
                # find matching protecode product (aka app)
                for existing_product in existing_products:
                    if existing_product.custom_data().get('IMAGE_VERSION') == \
                      container_image.version():
                        existing_products.remove(existing_product)
                        protecode_apps_to_consider.append(existing_product)
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
                # scan_result here is an AnalysisResult which lacks our metadata. We need the
                # metadata to fetch the image version. Therefore, fetch the proper result
                scan_result = self._api.scan_result(product_id=protecode_app.product_id())
                image_version = scan_result.custom_data().get('IMAGE_VERSION')
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
            try:
                scan_result = self._upload_image(
                    component=container_image_group.component(),
                    container_image=container_image,
                )
            except requests.exceptions.HTTPError as e:
                # in case the image is currently being scanned, Protecode will answer with HTTP
                # code 409 ('conflict'). In this case, fetch the ongoing scan to add it
                # to the list of scans to consider. In all other cases re-raise the error.
                if e.response.status_code != requests.codes.conflict:
                    raise e
                scan_result = self.retrieve_scan_result(
                    component=container_image_group.component(),
                    container_image=container_image,
                )

            protecode_apps_to_consider.append(scan_result)

        # wait for all apps currently being scanned
        for protecode_app in protecode_apps_to_consider:
            # replace - potentially incomplete - scan result
            protecode_apps_to_consider.remove(protecode_app)
            ci.util.info(f'waiting for {protecode_app.product_id()}')
            protecode_apps_to_consider.append(
                self._api.wait_for_scan_result(protecode_app.product_id())
            )

        # apply imported triages for all protecode apps
        for protecode_app in protecode_apps_to_consider:
            product_id = protecode_app.product_id()
            self._transport_triages(triages_to_import, product_id)

        # apply triages from GCR
        for protecode_app in protecode_apps_to_consider:
            self._import_triages_from_gcr(protecode_app)

        # yield results
        for protecode_app in protecode_apps_to_consider:
            scan_result = self._api.scan_result(protecode_app.product_id())
            yield mk_upload_result(
                status=UploadStatus.DONE, # XXX remove this
                result=scan_result,
                container_image=container_image,
            )

        # rm all outdated protecode apps
        for protecode_app in protecode_apps_to_remove:
            product_id = protecode_app.product_id()
            self._api.delete_product(product_id=product_id)
            ci.util.info(f'purged outdated product {product_id}')

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

    def _import_triages_from_gcr(self, scan_result: AnalysisResult):
        image_ref = scan_result.custom_data().get('IMAGE_REFERENCE', None)
        if not image_ref:
            logging.warning(f'no image-ref-name custom-prop for {scan_result.product_id()}')
            return scan_result

        if not ccc.grafeas.scan_available(image_reference=image_ref):
            ci.util.warning(f'no scan result available in gcr: {image_ref}')
            return scan_result

        # determine worst CVE according to GCR's data
        worst_cvss = -1
        try:
            for gcr_occ in ccc.grafeas.filter_vulnerabilities(
                image_reference=image_ref,
                cvss_threshold=self.cvss_threshold,
            ):
                gcr_score = gcr_occ.vulnerability.cvss_score
                worst_cvss = max(worst_cvss, gcr_score)
        except ccc.grafeas.VulnerabilitiesRetrievalFailed as vrf:
            ci.util.warning(str(vrf))
            # warn, but ignore
            return scan_result

        if worst_cvss >= self.cvss_threshold:
            ci.util.info(f'GCR\'s worst CVSS rating is above threshold: {worst_cvss}')
            return scan_result # do not import triages (although we could, considering components)

        # if this line is reached, all vulnerabilities are considered to be less severe than
        # protecode thinks. So triage all of them away
        for component in scan_result.components():
            for vulnerability in component.vulnerabilities():
                severity = float(vulnerability.cve_severity_str(protecode.model.CVSSVersion.V3))
                if severity < self.cvss_threshold:
                    continue # only triage vulnerabilities above threshold
                if vulnerability.has_triage():
                    continue # nothing to do

                version = component.version()
                if not version:
                    version = 'unknown'

                triage_dict = {
                    'component': component.name(),
                    'version': version,
                    'vulns': [vulnerability.cve()],
                    'scope': protecode.model.TriageScope.RESULT.value,
                    'reason': 'OT', # "other"
                    'description': f'[ci] imported from GCR {image_ref}',
                    'product_id': scan_result.product_id(),
                }

                self._api.add_triage_raw(triage_dict=triage_dict)
                ci.util.info(f'added triage: {component.name()}:{vulnerability.cve()}')

        return scan_result
