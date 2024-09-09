import collections
import collections.abc
import dataclasses
import datetime
import functools
import logging

import botocore.exceptions
import dateutil.parser
import pytz
import requests

import ci.log
import cnudie.access
import cnudie.iter
import cnudie.retrieve
import concourse.model.traits.image_scan as image_scan
import delivery.client
import dso.cvss
import dso.labels
import dso.model
import gci.componentmodel as cm
import gci.oci
import oci.client
import protecode.assessments
import protecode.client
import protecode.model as pm
import protecode.rescore
import protecode.util
import tarutil


logger = logging.getLogger(__name__)
ci.log.configure_default_logging(print_thread_id=True)


@functools.lru_cache(maxsize=200)
def _wait_for_scan_result(
    protecode_client: protecode.client.ProtecodeApi,
    product_id: int,
) -> pm.AnalysisResult:
    return protecode_client.wait_for_scan_result(product_id=product_id)


class ResourceGroupProcessor:
    def __init__(
        self,
        protecode_client: protecode.client.ProtecodeApi,
        oci_client: oci.client.Client,
        group_id: int=None,
        reference_group_ids: collections.abc.Sequence[int]=(),
        cvss_threshold: float=7.0,
    ):
        self.protecode_client = protecode_client
        self.oci_client = oci_client
        self.group_id = group_id
        self.reference_group_ids = reference_group_ids
        self.cvss_threshold = cvss_threshold

    def _products_with_relevant_triages(
        self,
        resource_node: cnudie.iter.ResourceNode,
    ) -> collections.abc.Generator[pm.Product, None, None]:
        relevant_group_ids = set(self.reference_group_ids)
        relevant_group_ids.add(self.group_id)

        metadata = protecode.util.component_artifact_metadata(
            component=resource_node.component,
            artefact=resource_node.resource,
            # we want to find all possibly relevant scans, so omit all version data
            omit_resource_version=True,
            oci_client=self.oci_client,
        )

        for id in relevant_group_ids:
            products = list(self.protecode_client.list_apps(
                group_id=id,
                custom_attribs=metadata,
            ))
            yield from products

    def iter_products(
        self,
        products_to_import_from: list[pm.Product],
        use_product_cache: bool=True,
        delete_inactive_products_after_seconds: int=None,
    ) -> collections.abc.Generator[
        tuple[pm.Component, pm.Vulnerability, tuple[pm.Triage]],
        None,
        None,
    ]:
        '''
        Used to retrieve the triages of the supplied products grouped by components and
        their vulnerabilities. Also, if `delete_inactive_products_after` is set, old
        bdba products will be deleted according to it.
        Note: `delete_inactive_products_after` must be greater than the interval in which
        the resources are set or otherwise the products are going to be deleted immediately.
        Also, old products of resources which are not scanned anymore at all (meaning in no
        version) are _not_ going to be deleted.
        '''
        def _iter_vulnerabilities(
            result: pm.AnalysisResult,
        ) -> collections.abc.Generator[tuple[pm.Component, pm.Vulnerability], None, None]:
            for component in result.components():
                for vulnerability in component.vulnerabilities():
                    yield component, vulnerability

        def iter_vulnerabilities_with_assessments(
            result: pm.AnalysisResult,
        ):
            for component, vulnerability in _iter_vulnerabilities(result=result):
                if not vulnerability.has_triage():
                    continue
                yield component, vulnerability, tuple(vulnerability.triages())

        now = datetime.datetime.now(tz=pytz.UTC)
        delete_after = now + datetime.timedelta(
            seconds=delete_inactive_products_after_seconds or 0,
        )
        for product in products_to_import_from:
            if delete_inactive_products_after_seconds is not None:
                if not (delete_after_flag := product.custom_data().get('DELETE_AFTER')):
                    delete_after_flag = delete_after.isoformat()
                    self.protecode_client.set_metadata(
                        product_id=product.product_id(),
                        custom_attribs={
                            'DELETE_AFTER': delete_after_flag,
                        },
                    )

                if now >= dateutil.parser.isoparse(delete_after_flag):
                    self.protecode_client.delete_product(product_id=product.product_id())
                    logger.info(f'deleted old bdba product {product.product_id()}')
                    continue

            if use_product_cache:
                result = _wait_for_scan_result(
                    protecode_client=self.protecode_client,
                    product_id=product.product_id(),
                )
            else:
                result = self.protecode_client.wait_for_scan_result(
                    product_id=product.product_id(),
                )
            yield from iter_vulnerabilities_with_assessments(
                result=result,
            )

    def scan_request(
        self,
        resource_node: cnudie.iter.ResourceNode,
        known_artifact_scans: tuple[pm.Product],
        s3_client: 'botocore.client.S3',
    ) -> pm.ScanRequest:
        component = resource_node.component
        resource = resource_node.resource

        display_name = f'{resource.name}_{resource.version}_{component.name}'.replace('/', '_')

        if resource.type is cm.ArtefactType.OCI_IMAGE:
            # find product existing bdba scans (if any)
            component_artifact_metadata = protecode.util.component_artifact_metadata(
                component=component,
                artefact=resource,
                omit_resource_version=False,
                oci_client=self.oci_client
            )
            target_product_id = protecode.util._matching_analysis_result_id(
                component_artifact_metadata=component_artifact_metadata,
                analysis_results=known_artifact_scans,
            )
            if target_product_id:
                logger.info(f'{display_name=}: found {target_product_id=}')
            else:
                logger.info(f'{display_name=}: did not find old scan')

            def iter_content():
                image_reference = gci.oci.image_ref_with_digest(
                    image_reference=resource.access.imageReference,
                    digest=resource.digest,
                    oci_client=self.oci_client,
                )
                yield from oci.image_layers_as_tarfile_generator(
                    image_reference=image_reference,
                    oci_client=self.oci_client,
                    include_config_blob=False,
                    fallback_to_first_subimage_if_index=True,
                )

            return pm.ScanRequest(
                component=component,
                artefact=resource,
                scan_content=iter_content(),
                display_name=display_name,
                target_product_id=target_product_id,
                custom_metadata=component_artifact_metadata,
            )
        elif resource.type == 'application/tar+vm-image-rootfs':
            # hardcoded semantics for vm-images:
            # merge all appropriate (tar)artifacts into one big tararchive
            component_artifact_metadata = protecode.util.component_artifact_metadata(
                component=component,
                artefact=resource,
                omit_resource_version=False,
                oci_client=self.oci_client
            )
            target_product_id = protecode.util._matching_analysis_result_id(
                component_artifact_metadata=component_artifact_metadata,
                analysis_results=known_artifact_scans,
            )

            if target_product_id:
                logger.info(f'{display_name=}: found {target_product_id=}')
            else:
                logger.info(f'{display_name=}: did not find old scan')

            # hardcode assumption about all accesses being of s3-type
            def as_blob_descriptors():
                name = resource.extraIdentity.get('platform', 'dummy')

                access: cm.S3Access = resource.access
                yield cnudie.access.s3_access_as_blob_descriptor(
                    s3_client=s3_client,
                    s3_access=access,
                    name=name,
                )

            return pm.ScanRequest(
                component=component,
                artefact=resource,
                scan_content=tarutil.concat_blobs_as_tarstream(
                    blobs=as_blob_descriptors(),
                ),
                display_name=display_name,
                target_product_id=target_product_id,
                custom_metadata=component_artifact_metadata,
            )
        else:
            raise NotImplementedError(resource.type)

    def process_scan_request(
        self,
        scan_request: pm.ScanRequest,
        processing_mode: pm.ProcessingMode,
    ) -> pm.AnalysisResult:
        def raise_on_error(exception):
            raise pm.BdbaScanError(
                scan_request=scan_request,
                component=scan_request.component,
                artefact=scan_request.artefact,
                exception=exception,
            )

        if processing_mode is pm.ProcessingMode.FORCE_UPLOAD:
            if (product_id := scan_request.target_product_id):
                # reupload binary
                try:
                    return self.protecode_client.upload(
                        application_name=scan_request.display_name,
                        group_id=self.group_id,
                        data=scan_request.scan_content,
                        replace_id=product_id,
                        custom_attribs=scan_request.custom_metadata,
                    )
                except requests.exceptions.HTTPError as e:
                    raise_on_error(e)
                except botocore.exceptions.BotoCoreError as e:
                    raise_on_error(e)
            else:
                # upload new product
                try:
                    return self.protecode_client.upload(
                        application_name=scan_request.display_name,
                        group_id=self.group_id,
                        data=scan_request.scan_content,
                        custom_attribs=scan_request.custom_metadata,
                    )
                except requests.exceptions.HTTPError as e:
                    raise_on_error(e)
                except botocore.exceptions.BotoCoreError as e:
                    raise_on_error(e)
        elif processing_mode is pm.ProcessingMode.RESCAN:
            if (existing_id := scan_request.target_product_id):
                # check if result can be reused
                scan_result = self.protecode_client.scan_result(product_id=existing_id)
                if scan_result.is_stale() and not scan_result.has_binary():
                    # no choice but to upload
                    try:
                        return self.protecode_client.upload(
                            application_name=scan_request.display_name,
                            group_id=self.group_id,
                            data=scan_request.scan_content,
                            replace_id=existing_id,
                            custom_attribs=scan_request.custom_metadata,
                        )
                    except requests.exceptions.HTTPError as e:
                        raise_on_error(e)
                    except botocore.exceptions.BotoCoreError as e:
                        raise_on_error(e)

                # update name unless identical
                if scan_result.name() != scan_request.display_name:
                    self.protecode_client.set_product_name(
                        product_id=existing_id,
                        name=scan_request.display_name,
                    )
                # update metadata if new metadata is not completely included in current one
                if scan_result.custom_data().items() < scan_request.custom_metadata.items():
                    self.protecode_client.set_metadata(
                        product_id=existing_id,
                        custom_attribs=scan_request.custom_metadata,
                    )

                if scan_result.has_binary() and scan_result.is_stale():
                    # binary is still available, and "result is stale" (there was an engine-
                    # update), trigger rescan
                    logger.info(
                        f'Triggering rescan for {existing_id} ({scan_request.display_name()})'
                    )
                    self.protecode_client.rescan(product_id=existing_id)
                try:
                    return self.protecode_client.scan_result(product_id=existing_id)
                except requests.exceptions.HTTPError as e:
                    raise_on_error(e)
                except botocore.exceptions.BotoCoreError as e:
                    raise_on_error(e)
            else:
                try:
                    return self.protecode_client.upload(
                        application_name=scan_request.display_name,
                        group_id=self.group_id,
                        data=scan_request.scan_content,
                        custom_attribs=scan_request.custom_metadata,
                    )
                except requests.exceptions.HTTPError as e:
                    raise_on_error(e)
                except botocore.exceptions.BotoCoreError as e:
                    raise_on_error(e)
        else:
            raise NotImplementedError(processing_mode)

    def process(
        self,
        resource_node: cnudie.iter.ResourceNode,
        known_scan_results: tuple[pm.Product],
        s3_client: 'botocore.client.S3',
        processing_mode: pm.ProcessingMode,
        delivery_client: delivery.client.DeliveryServiceClient=None,
        license_cfg: image_scan.LicenseCfg=None,
        cve_rescoring_rules: tuple[dso.cvss.RescoringRule]=tuple(),
        auto_assess_max_severity: dso.cvss.CVESeverity=dso.cvss.CVESeverity.MEDIUM,
        use_product_cache: bool=True,
        delete_inactive_products_after_seconds: int=None,
    ) -> collections.abc.Generator[dso.model.ArtefactMetadata, None, None]:
        resource = resource_node.resource
        component = resource_node.component

        products_to_import_from = list(self._products_with_relevant_triages(
            resource_node=resource_node,
        ))

        assessments = self.iter_products(
            products_to_import_from=products_to_import_from,
            use_product_cache=use_product_cache,
            delete_inactive_products_after_seconds=delete_inactive_products_after_seconds,
        )

        scan_request = self.scan_request(
            resource_node=resource_node,
            known_artifact_scans=known_scan_results,
            s3_client=s3_client,
        )
        try:
            scan_result = self.process_scan_request(
                scan_request=scan_request,
                processing_mode=processing_mode,
            )
            scan_result = self.protecode_client.wait_for_scan_result(scan_result.product_id())
            scan_failed = False
        except pm.BdbaScanError as bse:
            scan_result = bse
            scan_failed = True
            logger.warning(bse.print_stacktrace())

        # don't include component version here since it is also not considered in the BDBA scan
        # -> this will deduplicate findings of the same artefact version across different
        # component versions
        component = dataclasses.replace(scan_request.component, version=None)
        resource = scan_request.artefact
        scanned_element = cnudie.iter.ResourceNode(
            path=(cnudie.iter.NodePathEntry(component),),
            resource=resource,
        )

        if scan_failed:
            logger.error(f'scan of {scanned_element=} failed; {scan_result=}')
            return

        logger.info(
            f'scan of {scan_result.display_name()} succeeded, going to post-process results'
        )

        if version_hints := _package_version_hints(
            component=component,
            artefact=resource,
            result=scan_result,
        ):
            logger.info(f'uploading package-version-hints for {scan_result.display_name()}')
            scan_result = protecode.assessments.upload_version_hints(
                scan_result=scan_result,
                hints=version_hints,
                client=self.protecode_client,
            )

        assessed_vulns_by_component = collections.defaultdict(list)

        if scan_request.auto_triage_scan():
            assessed_vulns_by_component = protecode.assessments.auto_triage(
                analysis_result=scan_result,
                protecode_client=self.protecode_client,
                assessed_vulns_by_component=assessed_vulns_by_component,
            )

        assessed_vulns_by_component = protecode.assessments.add_assessments_if_none_exist(
            tgt=scan_result,
            tgt_group_id=self.group_id,
            assessments=assessments,
            protecode_client=self.protecode_client,
            assessed_vulns_by_component=assessed_vulns_by_component,
        )

        if cve_rescoring_rules:
            assessed_vulns_by_component = protecode.rescore.rescore(
                bdba_client=self.protecode_client,
                scan_result=scan_result,
                scanned_element=scanned_element,
                rescoring_rules=cve_rescoring_rules,
                max_rescore_severity=auto_assess_max_severity,
                assessed_vulns_by_component=assessed_vulns_by_component,
            )

        if assessed_vulns_by_component:
            logger.info(
                f'retrieving result again from bdba for {scan_result.display_name()} ' +
                '(this may take a while)'
            )
            scan_result = self.protecode_client.wait_for_scan_result(
                product_id=scan_result.product_id(),
            )

        if delete_inactive_products_after_seconds is not None:
            # remove deletion flag for current product as it is still in use
            self.protecode_client.set_metadata(
                product_id=scan_result.product_id(),
                custom_attribs={
                    'DELETE_AFTER': None,
                },
            )

        logger.info(f'post-processing of {scan_result.display_name()} done')

        yield from protecode.util.iter_artefact_metadata(
            scanned_element=scanned_element,
            scan_result=scan_result,
            license_cfg=license_cfg,
            delivery_client=delivery_client,
        )


def _package_version_hints(
    component: cm.Component,
    artefact: cm.Artifact,
    result: pm.AnalysisResult,
) -> list[dso.labels.PackageVersionHint] | None:
    def result_matches(resource: cm.Resource, result: pm.AnalysisResult):
        '''
        find matching result for package-version-hint
        note: we require strict matching of resource-version
        '''
        cd = result.custom_data()
        if not cd.get('COMPONENT_NAME') == component.name:
            return False
        if not cd.get('IMAGE_REFERENCE_NAME') == artefact.name:
            return False
        if not cd.get('IMAGE_VERSION') == artefact.version:
            return False

        return True

    if not result_matches(resource=artefact, result=result):
        return None

    if not isinstance(artefact, cm.Resource):
        raise NotImplementedError(artefact)

    artefact: cm.Resource

    package_hints_label = artefact.find_label(name=dso.labels.PackageVersionHintLabel.name)
    if not package_hints_label:
        return None

    return [
        dso.labels.PackageVersionHint(
            name=hint.get('name'),
            version=hint.get('version'),
        ) for hint in package_hints_label.value
    ]


def retrieve_existing_scan_results(
    protecode_client: protecode.client.ProtecodeApi,
    group_id: int,
    resource_node: cnudie.iter.ResourceNode,
    oci_client: oci.client.Client,
) -> list[pm.Product]:
    query_data = protecode.util.component_artifact_metadata(
        component=resource_node.component,
        artefact=resource_node.resource,
        omit_resource_version=True,
        oci_client=oci_client,
    )

    return list(protecode_client.list_apps(
        group_id=group_id,
        custom_attribs=query_data,
    ))
