import enum
import logging
import textwrap
import typing

import requests
import requests.exceptions

import oci

import ccc.gcp
import ccc.oci
import ci.util
import cnudie.util
import gci.componentmodel
import protecode.model
import version


from protecode.client import ProtecodeApi
from protecode.model import (
    AnalysisResult,
    TriageScope,
    UploadResult,
    UploadStatus,
    VersionOverrideScope,
)
from concourse.model.base import (
    AttribSpecMixin,
    AttributeSpec,
)
from ccc.grafeas_model import (
    Severity,
)
from ci.util import not_none, warning, check_type, info


logger = logging.getLogger(__name__)


class ResourceGroup:
    '''
    A set of Resources representing an OCI image sharing a common declaring component and a common
    logical name.

    Resource Groups are intended to be handled as "virtual Protecode Groups".
    This particularly means they share triages.

    As a very common "special case", a resource group may contain exactly one container image.

    @param component: the Component declaring dependency towards the given images
    @param resources: iterable of Resources; must share logical name
    '''
    def __init__(
        self,
        component,
        resources: typing.Iterable[gci.componentmodel.Resource],
    ):
        # TODO: Validate resource type?
        self._component = component

        unique_resources = []
        known_accesses = set()

        for resource in resources:
            if (acc := resource.access) not in known_accesses:
                known_accesses.add(acc)
                unique_resources.append(resource)

        self._resources = unique_resources

        if not len(self._resources) > 0:
            raise ValueError('at least one container image must be given')

        # do not try to parse in case no sorting is required
        if len(resources) > 1:
            # sort, smallest version first
            self._resources = sorted(
                self._resources,
                key=lambda r: version.parse_to_semver(r.version),
            )

        image_name = {r.name for r in self._resources}
        if len(image_name) > 1:
            raise ValueError(
                f'All images must share same name. Found more than one name: {image_name}'
            )
        self._image_name = image_name.pop()

        # todo: also validate all images are in fact declared by given component

    def component(self):
        return self._component

    def image_name(self):
        return self._image_name

    def resources(self):
        '''
        @returns sorted iterable containing all resources (smallest version first)
        '''
        return [r for r in self._resources]

    # TODO: why though? Use images() or iterator consistently, remove the other one
    def __iter__(self):
        return self.resources().__iter__()


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


class ProtecodeUtil:
    def __init__(
            self,
            protecode_api: ProtecodeApi,
            processing_mode: ProcessingMode=ProcessingMode.RESCAN,
            group_id: int=None,
            reference_group_ids=(),
            cvss_threshold: int=7,
            effective_severity_threshold: Severity=Severity.SEVERITY_UNSPECIFIED,
    ):
        protecode_api.login()
        self._processing_mode = check_type(processing_mode, ProcessingMode)
        self._api = not_none(protecode_api)
        self._group_id = group_id
        self._reference_group_ids = reference_group_ids
        self.cvss_threshold = cvss_threshold
        self.effective_severity_threshold = Severity(effective_severity_threshold)

    def _image_group_metadata(
        self,
        component_name: str,
        resource_name: str,
    ):
        return {
            'COMPONENT_NAME': component_name,
            'IMAGE_REFERENCE_NAME': resource_name,
        }

    def _image_ref_metadata(
        self,
        resource: gci.componentmodel.Resource,
        omit_version: bool,
    ):
        metadata_dict = {
            'IMAGE_REFERENCE_NAME': resource.name,
            'RESOURCE_TYPE': resource.type.value,
        }
        if not omit_version:
            oci_client = ccc.oci.oci_client()
            img_ref_with_digest = oci_client.to_digest_hash(
                image_reference=resource.access.imageReference,
            )
            digest = img_ref_with_digest.split('@')[-1]
            metadata_dict['IMAGE_REFERENCE'] = resource.access.imageReference
            metadata_dict['IMAGE_VERSION'] = resource.version
            metadata_dict['IMAGE_DIGEST'] = digest
            metadata_dict['DIGEST_IMAGE_REFERENCE'] = str(img_ref_with_digest)

        return metadata_dict

    def _component_metadata(
        self,
        component: gci.componentmodel.Component,
        omit_version=True,
    ):
        metadata = {'COMPONENT_NAME': component.name}
        if not omit_version:
            metadata['COMPONENT_VERSION'] = component.version

        return metadata

    def _upload_name(
        self,
        resource: gci.componentmodel.Resource,
        component: gci.componentmodel.Component,
    ):
        image_reference = resource.access.imageReference
        image_path, image_tag = image_reference.split(':')
        image_name = resource.name
        return '{i}_{v}_{c}'.format(
            i=image_name,
            v=image_tag,
            c=component.name,
        )

    def _update_product_name(self, product_id: int, upload_name: str):
        scan_result = self._api.scan_result_short(product_id=product_id)
        current_name = scan_result.name()

        if current_name == upload_name:
            return # nothing to do

        self._api.set_product_name(product_id=product_id, name=upload_name)

    def _metadata(
            self,
            resource: gci.componentmodel.Resource,
            component: gci.componentmodel.Component,
            omit_version: bool,
        ):
        metadata = self._image_ref_metadata(resource, omit_version=omit_version)
        metadata.update(self._component_metadata(component=component, omit_version=omit_version))
        return metadata

    def process_component_resources(
        self,
        component_resources: list[cnudie.util.ComponentResource],
    ) -> typing.Iterable[UploadResult]:

        # depending on upload-mode, determine an upload-action for each related image
        # - resources to upload
        # - protecode-apps to remove
        # - triages to import
        product_id_to_resource: dict[str, cnudie.util.ComponentResource] = dict()
        protecode_products_to_consider = list() # consider to rescan; return results
        protecode_products_to_remove = set()
        component_resources_to_upload = list()
        triages_to_import = set()

        # HACK since the component and resource name are the same for all elements
        # (only the component and resource version differ) we can use the names for all elements
        component_name = component_resources[0].component.name
        resource_name = component_resources[0].resource.name

        logger.info(
            f'Processing component resource group for {component_name=} and {resource_name=}'
        )

        metadata = self._image_group_metadata(
            component_name=component_name,
            resource_name=resource_name,
        )

        existing_protecode_products = self._api.list_apps(
            group_id=self._group_id,
            custom_attribs=metadata,
        )

        # import triages from local group
        scan_results = (
            self._api.scan_result(product_id=product.product_id())
            for product in existing_protecode_products
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
        logger.info(f'found {len(triages_to_import)} triage(s) to import')

        # process resources according to processing mode
        if self._processing_mode is ProcessingMode.FORCE_UPLOAD:
            logger.info('force-upload - will re-upload all images')
            component_resources_to_upload += list(component_resources)
            # remove all
            protecode_products_to_remove = set(existing_protecode_products)
        elif self._processing_mode is ProcessingMode.RESCAN:
            for component_resource in component_resources:
                resource = component_resource.resource
                logger.info(
                    f'Checking whether a product for {resource.access.imageReference} exists.'
                )
                component_version = component_resource.component.version
                # find matching protecode product
                for existing_product in existing_protecode_products:
                    product_image_digest = existing_product.custom_data().get('IMAGE_DIGEST')
                    product_component_version = existing_product.custom_data().get(
                        'COMPONENT_VERSION'
                    )

                    oci_client = ccc.oci.oci_client()
                    img_ref_with_digest = oci_client.to_digest_hash(
                        image_reference=resource.access.imageReference,
                    )
                    digest = img_ref_with_digest.split('@')[-1]
                    if (
                        product_image_digest == digest
                        and product_component_version == component_version
                    ):
                        existing_protecode_products.remove(existing_product)
                        protecode_products_to_consider.append(existing_product)
                        product_id_to_resource[existing_product.product_id()] = component_resource
                        logger.info(
                            f"found product for '{resource.access.imageReference}' for "
                            f"component version '{component_version}': "
                            f'{existing_product.product_id()}'
                        )
                        break
                else:
                    logger.info(
                        f'did not find product for image {resource.access.imageReference} '
                        f'and version {component_version} - will upload'
                    )
                    # not found -> need to upload
                    component_resources_to_upload.append(component_resource)

            # all existing products that did not match shall be removed
            protecode_products_to_remove |= set(existing_protecode_products)
            if protecode_products_to_remove:
                logger.info(
                    'Marked existing product(s) with ID(s) '
                    f"'{','.join([str(p.product_id()) for p in protecode_products_to_remove])}' "
                    f"that had no match in the current group '{component_name}, {resource_name}' "
                    'for removal after triage transport.'
                )

        else:
            raise NotImplementedError()

        # trigger rescan if recommended
        for protecode_product in protecode_products_to_consider:
            scan_result = self._api.scan_result_short(product_id=protecode_product.product_id())

            if not scan_result.is_stale():
                continue # protecode does not recommend a rescan

            if not scan_result.has_binary():
                # scan_result lacks our metadata. We need the metadata to fetch the image version.
                # Therefore, fetch the proper result
                analysis_result = self._api.scan_result(product_id=protecode_product.product_id())
                image_digest = analysis_result.custom_data().get('IMAGE_DIGEST')
                # there should be at most one matching image (by image digest)
                oci_client = ccc.oci.oci_client()
                for component_resource in component_resources:
                    resource = component_resource.resource
                    digest = oci_client.to_digest_hash(
                        image_reference=resource.access.imageReference,
                    ).split('@')[-1]
                    if image_digest == digest:
                        logger.info(
                            f'{resource.access.imageReference=} no longer available '
                            'to protecode - will upload. '
                            f'Corresponding product: {protecode_product.product_id()}'
                        )
                        component_resources_to_upload.append(component_resource)
                        protecode_products_to_consider.remove(protecode_product)
                        # xxx - also add product for removal?
                        break
            else:
                logger.info(f'triggering rescan for {protecode_product.product_id()}')
                self._api.rescan(protecode_product.product_id())

        # upload new resources
        for component_resource in component_resources_to_upload:
            try:
                logger.info(
                    f'uploading resource with name {component_resource.resource.name} '
                    f'and version {component_resource.resource.version}'
                )
                scan_result = self._upload_resource(
                    component=component_resource.component,
                    resource=component_resource.resource,
                )
            except requests.exceptions.HTTPError as e:
                # in case the image is currently being scanned, Protecode will answer with HTTP
                # code 409 ('conflict'). In this case, fetch the ongoing scan to add it
                # to the list of scans to consider. In all other cases re-raise the error.
                if e.response.status_code != requests.codes.conflict:
                    raise e

                image_ref = component_resource.resource.access.imageReference
                logger.warning(f'conflict whilst trying to upload {image_ref=}')

                scan_result = self.retrieve_scan_result(
                    component=component_resource.component,
                    resource=component_resource.resource,
                )

            if not scan_result:
                print(f'{component_resource=}')

            product_id_to_resource[scan_result.product_id()] = component_resource
            protecode_products_to_consider.append(scan_result)

        def wait_for_scan_to_finish(protecode_products_to_consider):
            for protecode_product in protecode_products_to_consider:
                logger.info(f'waiting for {protecode_product.product_id()}')
                yield self._api.wait_for_scan_result(protecode_product.product_id())
                logger.info(f'finished waiting for {protecode_product.product_id()}')

        analysis_results = list(wait_for_scan_to_finish(protecode_products_to_consider))

        # apply imported triages for all protecode products
        for analysis_result in analysis_results:
            product_id = analysis_result.product_id()
            existing_triages = list(self._existing_triages([analysis_result]))
            new_triages = [
                t for t in triages_to_import
                if t not in existing_triages
            ]
            logger.info(f'transporting triages for {analysis_result.product_id()}')
            self._transport_triages(new_triages, product_id)
            logger.info(f'done with transporting triages for {analysis_result.product_id()}')

        # apply triages from GCR
        for analysis_result in analysis_results:
            self._import_triages_from_gcr(analysis_result)

        # yield results
        for protecode_product in protecode_products_to_consider:
            analysis_result = self._api.scan_result(protecode_product.product_id())

            component_resource = product_id_to_resource[protecode_product.product_id()]

            # create closure for pdf retrieval to avoid actually having to store
            # all the pdf-reports in memory. Will be called when preparing to send
            # the notification emails if reports are to be included
            def pdf_retrieval_function():
                return self._api.pdf_report(protecode_product.product_id())

            yield UploadResult(
                component=component_resource.component,
                status=UploadStatus.DONE, # XXX remove this
                result=analysis_result,
                resource=component_resource.resource,
                pdf_report_retrieval_func=pdf_retrieval_function,
            )

        # in rare cases, we fail to find (again) an existing product, but through naming-convention
        # succeed in finding it implicitly while trying to upload image. Do not purge those
        # IDs (or in general: purge no ID we just recently created/retrieved)
        product_ids_not_to_purge = {app.product_id() for app in protecode_products_to_consider}

        # rm all outdated protecode apps
        for protecode_product in protecode_products_to_remove:
            product_id = protecode_product.product_id()
            if product_id in product_ids_not_to_purge:
                logger.warning(f'would have tried to purge {product_id=} - skipping')
                continue

            self._api.delete_product(product_id=product_id)
            logger.info(
                f'purged outdated product {product_id} '
                f'({protecode_product.display_name()})'
            )

    def retrieve_scan_result(
            self,
            resource: gci.componentmodel.Resource,
            component: gci.componentmodel.Component,
            group_id: int=None,
    ):
        metadata = self._metadata(
            resource=resource,
            component=component,
            omit_version=False,
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
            warning(f"found more than one product for image '{resource.access.imageReference}'")
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
        upload_name = self._upload_name(resource, component)
        self._update_product_name(product_id, upload_name)

        # retrieve existing product's details (list of products contained only subset of data)
        product = self._api.scan_result(product_id=product_id)
        return product

    def _upload_resource(
        self,
        component: gci.componentmodel.Component,
        resource: gci.componentmodel.Resource,
    ):
        metadata = self._metadata(
            resource=resource,
            component=component,
            omit_version=False,
        )

        # XXX need to check whether resource is actually a oci-resource
        image_reference = resource.access.imageReference

        oci_client = ccc.oci.oci_client()
        image_data = oci.image_layers_as_tarfile_generator(
            image_reference=image_reference,
            oci_client=oci_client
        )

        try:
            # Upload image and update outdated analysis result with the one triggered
            # by the upload.
            scan_result = self._api.upload(
                application_name=self._upload_name(
                    resource=resource,
                    component=component
                ).replace('/', '_'),
                group_id=self._group_id,
                data=image_data,
                custom_attribs=metadata,
            )
            return scan_result
        finally:
            pass # TODO: should deal w/ closing the streaming-rq on oci-client-side

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
        scan_result_triages = list(self._existing_triages([scan_result]))

        if not image_ref:
            logging.warning(f'no image-ref-name custom-prop for {scan_result.product_id()}')
            return scan_result

        grafeas_client = ccc.gcp.GrafeasClient.for_image(image_ref)

        if not grafeas_client.scan_available(image_reference=image_ref):
            logger.warning(f'no scan result available in gcr: {image_ref}')
            return scan_result

        # determine worst CVE according to GCR's data
        worst_cvss = -1
        worst_effective_vuln = Severity.SEVERITY_UNSPECIFIED
        try:
            vulnerabilities_from_grafeas = list(
                grafeas_client.filter_vulnerabilities(
                    image_reference=image_ref,
                    cvss_threshold=self.cvss_threshold,
                )
            )
            for gcr_occ in vulnerabilities_from_grafeas:
                gcr_score = gcr_occ.vulnerability.cvssScore
                worst_cvss = max(worst_cvss, gcr_score)
                effective_sev = gcr_occ.vulnerability.effectiveSeverity
                worst_effective_vuln = max(worst_effective_vuln, effective_sev)
        except ccc.gcp.VulnerabilitiesRetrievalFailed as vrf:
            logger.warning(str(vrf))
            # warn, but ignore
            return scan_result

        if worst_cvss >= self.cvss_threshold:
            logger.info(f'GCR\'s worst CVSS rating is above threshold: {worst_cvss}')
            logger.info(f'however, consider: {worst_effective_vuln=}  ({scan_result.product_id()})')
            triage_remainder = False
        else:
            # worst finding below our threshold -> we may safely triage everything
            # w/o being able to match triages component-wise
            triage_remainder = True

        def find_worst_vuln(
            component,
            vulnerability,
            grafeas_vulns
        ):
            component_name = component.name()
            cve_str = vulnerability.cve()

            worst_cve = -1
            worst_effective_severity = Severity.SEVERITY_UNSPECIFIED
            found_it = False
            for gv in grafeas_vulns:
                v = gv.vulnerability
                if v.shortDescription != cve_str: # TODO: could also check the note name
                    continue

                for pi in v.packageIssue:
                    v_name = pi.affectedPackage
                    if not v_name == component_name:
                        # XXX maybe we should be a bit more defensive, and check for CVE equality
                        # (if CVEs match, but compont name differs, a human could/should have a look)
                        if v.shortDescription == cve_str:
                            logger.warning(
                                f'XXX check if this is a match: {v_name} / {component_name}'
                            )
                        continue
                    found_it = True
                    # XXX should also check for version
                    worst_cve = max(worst_cve, v.cvssScore)
                    worst_effective_severity = max(worst_effective_severity, v.effectiveSeverity)

            return found_it, worst_cve, worst_effective_severity

        def find_component_version(component_name, occurrences):
            determined_version = None
            for occurrence in occurrences:
                package_issues = occurrence.vulnerability.packageIssue
                for package_issue in package_issues:
                    package_name = package_issue.affectedPackage
                    if package_name == component_name:
                        if (
                            determined_version is not None and
                            determined_version != package_issue.affectedVersion.fullName
                        ):
                            # found more than one possible version. Return None since we cannot
                            # be sure which version is correct
                            return None
                        determined_version = package_issue.affectedVersion.fullName
            return determined_version

        # if this line is reached, all vulnerabilities are considered to be less severe than
        # protecode thinks. So triage all of them away
        components_count = 0
        vulnerabilities_count = 0 # only above threshold, and untriaged
        skipped_due_to_historicalness = 0
        skipped_due_to_existing_triages = 0
        triaged_due_to_max_count = 0
        triaged_due_to_gcr_optimism = 0
        triaged_due_to_absent_count = 0

        # helper functon to avoid duplicating triages later
        def _triage_already_present(triage_dict, triages):
            for triage in triages:
                if triage.vulnerability_id() != triage_dict['vulns'][0]:
                    continue
                if triage.component_name() != triage_dict['component']:
                    continue
                if triage.description() != triage_dict['description']:
                    continue
                return True
            return False

        for component in scan_result.components():
            components_count += 1

            version = component.version()
            component_name = component.name()

            if not version:
                # if version is not known to protecode, see if it can be determined using gcr's
                # vulnerability-assessment
                if (version := find_component_version(component_name, vulnerabilities_from_grafeas)): # noqa:E203,E501
                    logger.info(
                        f"Grafeas has version '{version}' for undetermined protecode-component "
                        f"'{component_name}'. Will try to import version to Protecode."
                    )
                    try:
                        self._api.set_component_version(
                            component_name=component_name,
                            component_version=version,
                            objects=[o.sha1() for o in component.extended_objects()],
                            scope=VersionOverrideScope.APP,
                            app_id=scan_result.product_id(),
                        )
                    except requests.exceptions.HTTPError as http_err:
                        logger.warning(
                            f"Unable to set version for component '{component_name}': {http_err}."
                        )

            for vulnerability in component.vulnerabilities():

                vulnerabilities_count += 1

                severity = float(vulnerability.cve_severity_str(protecode.model.CVSSVersion.V3))
                if severity < self.cvss_threshold:
                    continue # only triage vulnerabilities above threshold
                if vulnerability.has_triage():
                    skipped_due_to_existing_triages += 1
                    continue # nothing to do
                if vulnerability.historical():
                    skipped_due_to_historicalness += 1
                    continue # historical vulnerabilities cannot be triaged.
                if not version:
                    # Protecode only allows triages for components with known version.
                    # set version to be able to triage away.
                    version = '[ci]-not-found-in-GCR'
                    logger.info(f"Setting dummy version for component '{component_name}'")
                    try:
                        self._api.set_component_version(
                            component_name=component_name,
                            component_version=version,
                            objects=[o.sha1() for o in component.extended_objects()],
                            scope=VersionOverrideScope.APP,
                            app_id=scan_result.product_id(),
                        )
                    except requests.exceptions.HTTPError as http_err:
                        logger.warning(
                            f"Unable to set version for component '{component_name}': {http_err}."
                        )
                        # version was not set - cannot triage
                        continue

                if not triage_remainder:
                    found_it, worst_cve, worst_eff = find_worst_vuln(
                        component=component,
                        vulnerability=vulnerability,
                        grafeas_vulns=vulnerabilities_from_grafeas,
                    )
                    if not found_it:
                        logger.info(
                            f'did not find {component.name()}:{vulnerability.cve()} in GCR'
                        )
                        triaged_due_to_absent_count += 1
                        description = \
                            '[ci] vulnerability was not reported by GCR'
                    elif worst_cve >= self.cvss_threshold:
                        triaged_due_to_gcr_optimism += 1
                        logger.info(
                            f'found {component.name()}, but is above threshold {worst_cve=}'
                        )
                        continue
                    else:
                        description = \
                            f'[ci] vulnerability was assessed by GCR with {worst_cve}'
                else:
                    triaged_due_to_max_count += 1
                    description = \
                        '[ci] vulnerability was not found by GCR'

                triage_dict = {
                    'component': component.name(),
                    'version': version,
                    'vulns': [vulnerability.cve()],
                    'scope': protecode.model.TriageScope.RESULT.value,
                    'reason': 'OT', # "other"
                    'description': description,
                    'product_id': scan_result.product_id(),
                }

                if _triage_already_present(triage_dict, scan_result_triages):
                    logger.info(f'triage {component.name()}:{vulnerability.cve()} already present.')
                    continue

                try:
                    self._api.add_triage_raw(triage_dict=triage_dict)
                    logger.info(f'added triage: {component.name()}:{vulnerability.cve()}')
                except requests.exceptions.HTTPError as http_err:
                    # since we are auto-importing anyway, be a bit tolerant
                    logger.warning(f'failed to add triage: {http_err}')

        logger.info(textwrap.dedent(f'''
            Product: {scan_result.display_name()} (ID: {scan_result.product_id()})
            Statistics: {components_count=} {vulnerabilities_count=}
            {skipped_due_to_historicalness=} {skipped_due_to_existing_triages=}
            {triaged_due_to_max_count=} {triaged_due_to_gcr_optimism=}
            {triaged_due_to_absent_count=}
        '''
        ))

        # retrieve scan-results again to get filtered results after taking triages into account
        return self._api.scan_result(product_id=scan_result.product_id())
