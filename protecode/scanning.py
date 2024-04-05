import collections
import collections.abc
import concurrent.futures
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
import oci.client
import protecode.assessments
import protecode.client
import protecode.model as pm
import protecode.rescore
import protecode.util
import tarutil


logger = logging.getLogger(__name__)
ci.log.configure_default_logging(print_thread_id=True)


def _resource_group_id(
    resource_group: tuple[cnudie.iter.ResourceNode],
) -> tuple[str, cm.ResourceIdentity, cm.ArtefactType|str]:
    '''
    return resource-id, identifying resource by
    - component-name ("component id)
    - resource-name, resource-version ("resource id")
    - resource-type
    '''
    r = resource_group[0]
    return tuple((
        r.component.name,
        (r.resource.name, r.resource.version),
        r.resource.type,
    ))


def find_related_groups(
    group: tuple[cnudie.iter.ResourceNode],
    groups: tuple[tuple[cnudie.iter.ResourceNode]],
    omit_resource_version: bool=False,
) -> collections.abc.Generator[tuple[cnudie.iter.ResourceNode], None, None]:
    group_representative = group[0]

    for g in groups:
        node = g[0]
        if group_representative.component.name != node.component.name:
            continue
        if group_representative.artefact.name != node.artefact.name:
            continue
        if group_representative.artefact.type != node.artefact.type:
            continue
        if (not omit_resource_version and
            group_representative.artefact.version != node.artefact.version):
            continue
        yield g


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
        resource_group: tuple[cnudie.iter.ResourceNode],
    ) -> collections.abc.Generator[pm.Product, None, None]:
        relevant_group_ids = set(self.reference_group_ids)
        relevant_group_ids.add(self.group_id)

        c = resource_group[0].component
        r = resource_group[0].resource

        metadata = protecode.util.component_artifact_metadata(
            component=c,
            artefact=r,
            # we want to find all possibly relevant scans, so omit all version data
            omit_component_version=True,
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

    def scan_requests(
        self,
        resource_group: tuple[cnudie.iter.ResourceNode],
        known_artifact_scans: dict[
            tuple[str, cm.ResourceIdentity, cm.ArtefactType|str],
            tuple[pm.Product],
        ],
        s3_client: 'botocore.client.S3',
    ) -> collections.abc.Generator[pm.ScanRequest, None, None]:
        c = resource_group[0].component
        r = resource_group[0].resource

        group_id = _resource_group_id(resource_group)
        group_name = f'{c.name}/{r.name}:{r.version} {r.type}'
        known_results = known_artifact_scans.get(group_id)
        display_name = f'{r.name}_{r.version}_{c.name}'.replace('/', '_')

        if r.type is cm.ResourceType.OCI_IMAGE:
            for resource_node in resource_group:
                c = resource_node.component
                r = resource_node.resource

                # find product existing bdba scans (if any)
                component_artifact_metadata = protecode.util.component_artifact_metadata(
                    component=c,
                    artefact=r,
                    omit_component_version=True,
                    omit_resource_version=False,
                    oci_client=self.oci_client
                )
                target_product_id = protecode.util._matching_analysis_result_id(
                    component_artifact_metadata=component_artifact_metadata,
                    analysis_results=known_results,
                )
                if target_product_id:
                    logger.info(f'{group_name=}: found {target_product_id=}')
                else:
                    logger.info(f'{group_name=}: did not find old scan')

                def iter_content():
                    image_reference = protecode.util.image_ref_with_digest(
                        image_reference=r.access.imageReference,
                        digest=r.digest,
                        oci_client=self.oci_client,
                    )
                    yield from oci.image_layers_as_tarfile_generator(
                        image_reference=image_reference,
                        oci_client=self.oci_client,
                        include_config_blob=False,
                        fallback_to_first_subimage_if_index=True,
                    )

                yield pm.ScanRequest(
                    component=c,
                    artefact=r,
                    scan_content=iter_content(),
                    display_name=display_name,
                    target_product_id=target_product_id,
                    custom_metadata=component_artifact_metadata,
                )
        elif r.type == 'application/tar+vm-image-rootfs':
            # hardcoded semantics for vm-images:
            # merge all appropriate (tar)artifacts into one big tararchive
            component_artifact_metadata = protecode.util.component_artifact_metadata(
                component=c,
                artefact=r,
                omit_component_version=True,
                omit_resource_version=False,
                oci_client=self.oci_client
            )
            target_product_id = protecode.util._matching_analysis_result_id(
                component_artifact_metadata=component_artifact_metadata,
                analysis_results=known_results,
            )

            if target_product_id:
                logger.info(f'{group_name=}: found {target_product_id=}')
            else:
                logger.info(f'{group_name=}: did not find old scan')

            # hardcode assumption about all accesses being of s3-type
            def as_blob_descriptors():
                for idx, resource in enumerate(resource_group):
                    name = resource.resource.extraIdentity.get('platform', str(idx))

                    access: cm.S3Access = resource.resource.access
                    yield cnudie.access.s3_access_as_blob_descriptor(
                        s3_client=s3_client,
                        s3_access=access,
                        name=name,
                    )

            yield pm.ScanRequest(
                component=c,
                artefact=r,
                scan_content=tarutil.concat_blobs_as_tarstream(
                    blobs=as_blob_descriptors(),
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
        resource_group: tuple[cnudie.iter.ResourceNode],
        processing_mode: pm.ProcessingMode,
        known_scan_results: dict[
            tuple[str, cm.ResourceIdentity, cm.ArtefactType|str],
            tuple[pm.Product],
        ],
        s3_client: 'botocore.client.S3',
        license_cfg: image_scan.LicenseCfg=None,
        cve_rescoring_rules: tuple[dso.cvss.RescoringRule]=tuple(),
        auto_assess_max_severity: dso.cvss.CVESeverity=dso.cvss.CVESeverity.MEDIUM,
        use_product_cache: bool=True,
        delete_inactive_products_after_seconds: int=None,
    ) -> collections.abc.Generator[dso.model.ArtefactMetadata, None, None]:
        r = resource_group[0].resource
        c = resource_group[0].component
        group_name = f'{c.name}/{r.name}:{r.version} - {r.type}'
        logger.info(f'Processing {group_name}')

        products_to_import_from = list(self._products_with_relevant_triages(
            resource_group=resource_group,
        ))
        # todo: deduplicate/merge assessments
        assessments = self.iter_products(
            products_to_import_from=products_to_import_from,
            use_product_cache=use_product_cache,
            delete_inactive_products_after_seconds=delete_inactive_products_after_seconds,
        )

        for scan_request in self.scan_requests(
            resource_group=resource_group,
            known_artifact_scans=known_scan_results,
            s3_client=s3_client,
        ):
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

            c = scan_request.component
            r = scan_request.artefact
            scanned_element = cnudie.iter.ResourceNode(
                path=(cnudie.iter.NodePathEntry(c),),
                resource=r,
            )

            if scan_failed:
                logger.error(f'scan of {scanned_element=} failed; {scan_result=}')
                return

            logger.info(
                f'scan of {scan_result.display_name()} succeeded, going to post-process results'
            )

            if version_hints := _package_version_hints(
                component=c,
                artefact=r,
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


def _retrieve_existing_scan_results(
    protecode_client: protecode.client.ProtecodeApi,
    group_id: int,
    resource_groups: collections.abc.Iterable[tuple[cnudie.iter.ResourceNode]],
    oci_client: oci.client.Client,
) -> dict[
    tuple[str, cm.ResourceIdentity, cm.ArtefactType|str],
    list[pm.Product],
]:
    # This function populates a dict that contains all relevant scans for all artifacts in a given
    # protecode group.
    # The created dict is later used to lookup existing scans when creating scan requests
    scan_results = dict()
    seen_apps = dict()
    for resource_group in resource_groups:
        # groups are assumed to belong to same component + resource version name, and type so it
        # is okay to choose first one as representative
        c = resource_group[0].component
        r = resource_group[0].resource

        query_data = protecode.util.component_artifact_metadata(
            component=c,
            artefact=r,
            omit_component_version=True,
            omit_resource_version=True,
            oci_client=oci_client,
        )

        meta = frozenset([(k, v) for k, v in query_data.items()])

        if not meta in seen_apps:
            seen_apps[meta] = list(protecode_client.list_apps(
                group_id=group_id,
                custom_attribs=query_data,
            ))

        resource_group_id = _resource_group_id(resource_group=resource_group)
        scan_results[resource_group_id] = seen_apps[meta]

    return scan_results


def _resource_groups(
    resource_nodes: collections.abc.Iterable[cnudie.iter.ResourceNode, None, None],
    filter_function: collections.abc.Callable[[cnudie.iter.ResourceNode], bool],
    artefact_types=(
        cm.ResourceType.OCI_IMAGE,
        'application/tar+vm-image-rootfs',
    ),
) -> collections.abc.Generator[tuple[cnudie.iter.ResourceNode], None, None]:
    '''
    group resources of same component name and resource version name

    this grouping is done in order to deduplicate identical resource versions shared between
    different component versions.
    '''

    # artefact-groups are grouped by:
    # component-name, resource-name, resource-version, resource-type
    # (thus implicitly deduplicating same resource-versions on different components)
    resource_groups: dict[
        tuple[str, cm.ResourceIdentity, str],
        list[cnudie.iter.ResourceNode],
    ] = collections.defaultdict(list)

    for resource_node in resource_nodes:
        if not resource_node.resource.type in artefact_types:
            continue

        if filter_function and not filter_function(resource_node):
            continue

        group_id = _resource_group_id((resource_node,))
        resource_groups[group_id].append(resource_node)

    logger.info(f'{len(resource_groups)=}')
    yield from (tuple(g) for g in resource_groups.values())


def upload_grouped_images(
    protecode_api: protecode.client.ProtecodeApi,
    component: cm.Component|cm.ComponentDescriptor,
    protecode_group_id=5,
    parallel_jobs=8,
    cve_threshold=7,
    processing_mode=pm.ProcessingMode.RESCAN,
    filter_function: collections.abc.Callable[[cnudie.iter.ResourceNode], bool]=(
        lambda node: True
    ),
    reference_group_ids=(),
    delivery_client: delivery.client.DeliveryServiceClient=None,
    oci_client: oci.client.Client=None,
    s3_client: 'botocore.client.S3'=None,
    license_cfg: image_scan.LicenseCfg=None,
    cve_rescoring_rules: tuple[dso.cvss.RescoringRule]=tuple(),
    auto_assess_max_severity: dso.cvss.CVESeverity=dso.cvss.CVESeverity.MEDIUM,
    yield_findings: bool=True,
    delete_inactive_products_after_seconds: int=None,
) -> collections.abc.Generator[dso.model.ArtefactMetadata, None, None] | None:
    # clear cache to make sure current scan execution retrieves latest bdba
    # products to copy existing assessments from
    _wait_for_scan_result.cache_clear()

    protecode_api.set_maximum_concurrent_connections(parallel_jobs)
    protecode_api.login()

    if isinstance(component, cm.ComponentDescriptor):
        component = component.component

    component_descriptor_lookup = cnudie.retrieve.create_default_component_descriptor_lookup(
        ocm_repository_lookup=cnudie.retrieve.ocm_repository_lookup(
            component.current_repository_ctx(),
        ),
        oci_client=oci_client,
        delivery_client=delivery_client,
    )

    groups = tuple(
        _resource_groups(
            resource_nodes=cnudie.iter.iter(
                component=component,
                lookup=component_descriptor_lookup,
                node_filter=cnudie.iter.Filter.resources,
            ),
            filter_function=filter_function,
        )
    )

    known_scan_results = _retrieve_existing_scan_results(
        protecode_client=protecode_api,
        group_id=protecode_group_id,
        resource_groups=groups,
        oci_client=oci_client,
    )
    processor = ResourceGroupProcessor(
        group_id=protecode_group_id,
        reference_group_ids=reference_group_ids,
        cvss_threshold=cve_threshold,
        protecode_client=protecode_api,
        oci_client=oci_client,
    )

    def task_function(
        resource_group: tuple[cnudie.iter.ResourceNode],
        processing_mode: pm.ProcessingMode,
    ) -> tuple[dso.model.ArtefactMetadata]:
        # only cache products if there is more than one resource group using them
        use_product_cache = len(tuple(find_related_groups(
            group=resource_group,
            groups=groups,
            omit_resource_version=True,
        ))) > 1
        return tuple(processor.process(
            resource_group=resource_group,
            processing_mode=processing_mode,
            known_scan_results=known_scan_results,
            s3_client=s3_client,
            license_cfg=license_cfg,
            cve_rescoring_rules=cve_rescoring_rules,
            auto_assess_max_severity=auto_assess_max_severity,
            use_product_cache=use_product_cache,
            delete_inactive_products_after_seconds=delete_inactive_products_after_seconds,
        ))

    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel_jobs) as tpe:
        # queue one execution per artifact group
        futures = {
            tpe.submit(task_function, g, processing_mode)
            for g in groups
        }
        for completed_future in concurrent.futures.as_completed(futures):
            results = completed_future.result()
            if delivery_client:
                delivery_client.update_metadata(data=results)
            else:
                logger.warning('Not uploading results to deliverydb, client not available')
            if yield_findings:
                yield from results

    _wait_for_scan_result.cache_clear()
