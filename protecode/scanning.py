import concurrent.futures
import logging
import typing

import botocore.exceptions
import github3.repos
import requests

import gci.componentmodel as cm

import ci.log
import cnudie.access
import cnudie.iter
import cnudie.retrieve
import cnudie.util
import concourse.model.traits.image_scan as image_scan
import delivery.client
import dso.labels
import github.compliance.model as gcm
import oci.client
import protecode.assessments
import protecode.client
import protecode.model as pm
import protecode.util
import tarutil


logger = logging.getLogger(__name__)
ci.log.configure_default_logging(print_thread_id=True)


def _resource_id(
    resource: cnudie.iter.ResourceNode,
) -> tuple[cm.ComponentIdentity, cm.ResourceIdentity, cm.ArtefactType|str]:
    '''
    return resource-id, identifying resource by
    - component-name, component-version ("component id)
    - resource-name, resource-version ("resource id")
    - resource-type
    '''
    return tuple((
        cm.ComponentIdentity(
            name=resource.component.name,
            version=resource.component.version,
        ),
        (resource.resource.name, resource.resource.version),
        resource.resource.type,
    ))


class ResourceGroupProcessor:
    def __init__(
        self,
        protecode_client: protecode.client.ProtecodeApi,
        group_id: int=None,
        reference_group_ids: typing.Sequence[int]=(),
        cvss_threshold: float=7.0,
    ):
        self.group_id = group_id
        self.reference_group_ids = reference_group_ids
        self.cvss_threshold = cvss_threshold
        self.protecode_client = protecode_client

    def _products_with_relevant_triages(
        self,
        resource: cnudie.iter.ResourceNode,
    ) -> typing.Iterator[pm.Product]:
        relevant_group_ids = set(self.reference_group_ids)
        relevant_group_ids.add(self.group_id)

        c = resource.component
        r = resource.resource

        metadata = protecode.util.component_artifact_metadata(
            component=c,
            artefact=r,
            # we want to find all possibly relevant scans, so omit all version data
            omit_component_version=True,
            omit_resource_version=True,
        )

        for id in relevant_group_ids:
            products = list(self.protecode_client.list_apps(
                group_id=id,
                custom_attribs=metadata,
            ))
            yield from products

    def iter_components_with_vulnerabilities_and_assessments(
        self,
        products_to_import_from: tuple[pm.Product],
    ) -> typing.Generator[tuple[pm.Component, pm.Vulnerability, tuple[pm.Triage]], None, None]:
        def _iter_vulnerabilities(
            result: pm.AnalysisResult,
        ) -> typing.Generator[tuple[pm.Component, pm.Vulnerability], None, None]:
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

        for product in products_to_import_from:
            result = self.protecode_client.wait_for_scan_result(product_id=product.product_id())

            yield from iter_vulnerabilities_with_assessments(
                result=result,
            )

    def scan_request(
        self,
        resource: cnudie.iter.ResourceNode,
        known_artifact_scans: dict[
            tuple[cm.ComponentIdentity, cm.ResourceIdentity, cm.ArtefactType|str],
            tuple[pm.Product],
        ],
        oci_client: oci.client.Client,
        s3_client: 'botocore.client.S3',
    ) -> pm.ScanRequest:
        c = resource.component
        r = resource.resource

        resource_id = _resource_id(resource)
        group_name = f'{c.name}:{c.version}/{r.name}:{r.version} {r.type}'
        known_results = known_artifact_scans.get(resource_id)
        display_name = f'{r.name}_{r.version}_{c.name}_{c.version}'.replace('/', '_')

        component_artifact_metadata = protecode.util.component_artifact_metadata(
            component=c,
            artefact=r,
            omit_component_version=False,
            omit_resource_version=False,
        )

        # find product existing bdba scans (if any)
        target_product_id = protecode.util._matching_analysis_result_id(
            component_artifact_metadata=component_artifact_metadata,
            analysis_results=known_results,
        )

        if target_product_id:
            logger.info(f'{group_name=}: found {target_product_id=}')
        else:
            logger.info(f'{group_name=}: did not find old scan')

        if r.type is cm.ArtefactType.OCI_IMAGE:
            def iter_content():
                image_reference = r.access.imageReference
                yield from oci.image_layers_as_tarfile_generator(
                    image_reference=image_reference,
                    oci_client=oci_client,
                    include_config_blob=False,
                    fallback_to_first_subimage_if_index=True,
                )

            return pm.ScanRequest(
                component=c,
                artefact=r,
                scan_content=iter_content(),
                display_name=display_name,
                target_product_id=target_product_id,
                custom_metadata=component_artifact_metadata,
            )
        elif (
            isinstance(r.type, str)
            and r.type == 'application/tar+vm-image-rootfs'
        ):
            # hardcode assumption about all accesses being of s3-type
            def as_blob_descriptor():
                name = r.extraIdentity.get('platform', '0')

                access: cm.S3Access = r.access
                return cnudie.access.s3_access_as_blob_descriptor(
                    s3_client=s3_client,
                    s3_access=access,
                    name=name,
                )

            return pm.ScanRequest(
                component=c,
                artefact=r,
                scan_content=tarutil.concat_blobs_as_tarstream(
                    blobs=[as_blob_descriptor()],
                ),
                display_name=display_name,
                target_product_id=target_product_id,
                custom_metadata=component_artifact_metadata,
            )
        else:
            raise NotImplementedError(r.type)

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

                # update name/metadata unless identical
                if scan_result.name() != scan_request.display_name:
                    self.protecode_client.set_product_name(
                        product_id=existing_id,
                        name=scan_request.display_name,
                    )
                if scan_result.custom_data() != scan_request.custom_metadata:
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
        resource: cnudie.iter.ResourceNode,
        processing_mode: pm.ProcessingMode,
        known_scan_results: dict[
            tuple[cm.ComponentIdentity, cm.ResourceIdentity, cm.ArtefactType|str],
            tuple[pm.Product],
        ],
        oci_client: oci.client.Client,
        s3_client: 'botocore.client.S3',
        license_cfg: image_scan.LicenseCfg=None,
        max_processing_days: gcm.MaxProcessingTimesDays=None,
        delivery_client: delivery.client.DeliveryServiceClient=None,
        repository: github3.repos.Repository=None,
    ) -> typing.Iterator[pm.BDBAScanResult]:
        r = resource.resource
        c = resource.component
        group_name = f'{c.name}:{c.version}/{r.name}:{r.version} - {r.type}'
        logger.info(f'Processing {group_name}')

        products_to_import_from = tuple(self._products_with_relevant_triages(
            resource=resource,
        ))
        # todo: deduplicate/merge assessments
        component_vulnerabilities_with_assessments = tuple(
            self.iter_components_with_vulnerabilities_and_assessments(
                products_to_import_from=products_to_import_from,
            )
        )

        scan_request = self.scan_request(
            resource=resource,
            known_artifact_scans=known_scan_results,
            oci_client=oci_client,
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

        state = gcm.ScanState.FAILED if scan_failed else gcm.ScanState.SUCCEEDED

        if scan_failed:
            # pylint: disable=E1123
            yield pm.BDBAScanResult(
                scanned_element=resource,
                status=pm.UploadStatus.DONE,
                result=scan_result,
                state=state,
            )
            return

        # scan succeeded
        logger.info(f'uploading package-version-hints for {scan_result.display_name()}')
        if version_hints := _package_version_hints(
            component=c,
            artefact=r,
            result=scan_result,
        ):
            protecode.assessments.upload_version_hints(
                scan_result=scan_result,
                hints=version_hints,
                client=self.protecode_client,
            )

        if scan_request.auto_triage_scan():
            protecode.assessments.auto_triage(
                analysis_result=scan_result,
                protecode_client=self.protecode_client,
            )

            scan_result = self.protecode_client.wait_for_scan_result(
                product_id=scan_result.product_id(),
            )

        protecode.assessments.add_assessments_if_none_exist(
            tgt=scan_result,
            tgt_group_id=self.group_id,
            assessments=component_vulnerabilities_with_assessments,
            protecode_client=self.protecode_client,
        )

        seen_license_names = set()
        for affected_package in scan_result.components():
            for vulnerability in affected_package.vulnerabilities():
                if vulnerability.historical():
                    continue
                vulnerability_scan_result = pm.VulnerabilityScanResult(
                    scanned_element=resource,
                    status=pm.UploadStatus.DONE,
                    result=scan_result,
                    state=state,
                    affected_package=affected_package,
                    vulnerability=vulnerability,
                )
                vulnerability_scan_result.calculate_latest_processing_date(
                    max_processing_days=max_processing_days,
                    delivery_svc_client=delivery_client,
                    repository=repository,
                )
                yield vulnerability_scan_result
            for license in affected_package.licenses:
                if license.name in seen_license_names:
                    continue
                seen_license_names.add(license.name)
                license_scan_result = pm.LicenseScanResult(
                    scanned_element=resource,
                    status=pm.UploadStatus.DONE,
                    result=scan_result,
                    state=state,
                    affected_package=affected_package,
                    license=license,
                    license_cfg=license_cfg,
                )
                license_scan_result.calculate_latest_processing_date(
                    max_processing_days=max_processing_days,
                    delivery_svc_client=delivery_client,
                    repository=repository,
                )
                yield license_scan_result
        yield pm.ComponentsScanResult(
            scanned_element=resource,
            status=pm.UploadStatus.DONE,
            result=scan_result,
            state=state,
        )


def _package_version_hints(
    component: cm.Component,
    artefact: cm.Artifact,
    result: pm.AnalysisResult,
) -> list[dso.labels.PackageVersionHint] | None:
    def result_matches(resource: cm.Resource, result: pm.AnalysisResult):
        '''
        find matching result for package-version-hint
        note: we require strict matching of both component-version and resource-version
        '''
        cd = result.custom_data()
        if not cd.get('COMPONENT_VERSION') == component.version:
            return False
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

    package_hints_label = dso.labels.deserialise_label(label=package_hints_label)
    package_hints_label: dso.labels.PackageVersionHintLabel

    package_hints = package_hints_label.value

    return package_hints


def _retrieve_existing_scan_results(
    protecode_client: protecode.client.ProtecodeApi,
    group_id: int,
    resources: tuple[cnudie.iter.ResourceNode],
) -> dict[
    tuple[cm.ComponentIdentity, cm.ResourceIdentity, cm.ArtefactType|str],
    list[pm.Product],
]:
    # This function populates a dict that contains all relevant scans for all artifacts in a given
    # protecode group.
    # The created dict is later used to lookup existing scans when creating scan requests
    scan_results = dict()
    for resource in resources:
        c = resource.component
        r = resource.resource

        query_data = protecode.util.component_artifact_metadata(
            component=c,
            artefact=r,
            omit_component_version=False,
            omit_resource_version=False,
        )

        scans = list(protecode_client.list_apps(
            group_id=group_id,
            custom_attribs=query_data,
        ))

        resource_id = _resource_id(resource=resource)
        scan_results[resource_id] = scans

    return scan_results


def _filter_resource_nodes(
    resource_nodes: typing.Generator[cnudie.iter.ResourceNode, None, None],
    filter_function: typing.Callable[[cnudie.iter.ResourceNode], bool],
    artefact_types=(
        cm.ResourceType.OCI_IMAGE,
        'application/tar+vm-image-rootfs',
    ),
) -> typing.Generator[cnudie.iter.ResourceNode, None, None]:
    for resource_node in resource_nodes:
        if not resource_node.resource.type in artefact_types:
            continue

        if filter_function and not filter_function(resource_node):
            continue

        yield resource_node


def upload_grouped_images(
    protecode_api: protecode.client.ProtecodeApi,
    bdba_cfg_name: str,
    component: cm.Component|cm.ComponentDescriptor,
    protecode_group_id=5,
    parallel_jobs=8,
    cve_threshold=7,
    processing_mode=pm.ProcessingMode.RESCAN,
    filter_function: typing.Callable[[cnudie.iter.ResourceNode], bool]=(
        lambda node: True
    ),
    reference_group_ids=(),
    delivery_client: delivery.client.DeliveryServiceClient=None,
    oci_client: oci.client.Client=None,
    s3_client: 'botocore.client.S3'=None,
    license_cfg: image_scan.LicenseCfg=None,
    max_processing_days: gcm.MaxProcessingTimesDays=None,
    repository: github3.repos.Repository=None,
) -> typing.Generator[pm.BDBAScanResult, None, None]:
    protecode_api.set_maximum_concurrent_connections(parallel_jobs)
    protecode_api.login()

    if isinstance(component, cm.ComponentDescriptor):
        component = component.component

    component_descriptor_lookup = cnudie.retrieve.create_default_component_descriptor_lookup(
        default_ctx_repo=component.current_repository_ctx(),
    )

    resources = tuple(
        _filter_resource_nodes(
            resource_nodes=cnudie.iter.iter(
                component=component,
                lookup=component_descriptor_lookup,
                node_filter=cnudie.iter.Filter.resources,
            ),
            filter_function=filter_function,
        )
    )
    logger.info(f'{len(resources)=}')

    known_scan_results = _retrieve_existing_scan_results(
        protecode_client=protecode_api,
        group_id=protecode_group_id,
        resources=resources,
    )
    processor = ResourceGroupProcessor(
        group_id=protecode_group_id,
        reference_group_ids=reference_group_ids,
        cvss_threshold=cve_threshold,
        protecode_client=protecode_api,
    )

    def task_function(
        resource: cnudie.iter.ResourceNode,
        processing_mode: pm.ProcessingMode,
    ) -> tuple[pm.BDBAScanResult]:
        return tuple(processor.process(
            resource=resource,
            processing_mode=processing_mode,
            known_scan_results=known_scan_results,
            oci_client=oci_client,
            s3_client=s3_client,
            license_cfg=license_cfg,
            max_processing_days=max_processing_days,
            delivery_client=delivery_client,
            repository=repository,
        ))

    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel_jobs) as tpe:
        # queue one execution per artifact group
        futures = {
            tpe.submit(task_function, r, processing_mode)
            for r in resources
        }
        for completed_future in concurrent.futures.as_completed(futures):
            scan_results = completed_future.result()
            if delivery_client:
                protecode.util.sync_results_with_delivery_db(
                    delivery_client=delivery_client,
                    results=scan_results,
                    bdba_cfg_name=bdba_cfg_name,
                )
            else:
                logger.warning('Not uploading results to deliverydb, client not available')
            yield from scan_results
