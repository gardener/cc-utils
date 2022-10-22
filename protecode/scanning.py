import concurrent.futures
import collections
import functools
import logging
import typing

import botocore.exceptions
import requests

import gci.componentmodel as cm

import ci.log
import cnudie.access
import cnudie.iter
import cnudie.retrieve
import cnudie.util
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


def _resource_group_id(resource_group: tuple[cnudie.iter.ResourceNode]):
    '''
    return resource-group-id, identifying resource-group by
    - component-name
    - resource-name, resource-version ("simple id")
    - resource-type
    '''
    r = resource_group[0]
    return tuple((
        r.component.name,
        (r.resource.name, r.resource.version),
        r.resource.type,
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
        resource_group: tuple[cnudie.iter.ResourceNode],
    ) -> typing.Iterator[pm.Product]:
        relevant_group_ids = set(self.reference_group_ids)
        relevant_group_ids.add(self.group_id)

        component = resource_group[0].component
        resource = resource_group[0].resource

        metadata = protecode.util.component_artifact_metadata(
            component=component,
            artefact=resource,
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
                result=result
            )

    def scan_requests(
        self,
        resource_group: tuple[cnudie.iter.ResourceNode],
        known_artifact_scans: typing.Dict[str, typing.Iterable[pm.Product]],
        oci_client: oci.client.Client,
        s3_client: 'botocore.client.S3',
    ) -> typing.Generator[pm.ScanRequest, None, None]:
        # assumption: resource-groups share same component(-name) and resource(name + version), as
        # well as resource-type
        # hard-coded special-handling:
        # - upload oci-images individually
        # - upload vm-images in a combined single upload
        component = resource_group[0].component
        resource = resource_group[0].resource
        resource_type = resource.type

        group_id = _resource_group_id(resource_group)
        group_name = f'{component.name}/{resource.name}:{resource.version} {resource.type}'
        known_results = known_artifact_scans.get(group_id)
        display_name = f'{resource.name}_{resource.version}_{component.name}'.replace(
            '/', '_'
        )

        if resource_type is cm.ResourceType.OCI_IMAGE:
            for resource_node in resource_group:
                component = resource_node.component
                resource = resource_node.resource

                # find product existing bdba scans (if any)
                component_artifact_metadata = protecode.util.component_artifact_metadata(
                    component=component,
                    artefact=resource,
                    omit_component_version=False,
                    omit_resource_version=False,
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
                    image_reference = resource.access.imageReference
                    yield from oci.image_layers_as_tarfile_generator(
                        image_reference=image_reference,
                        oci_client=oci_client,
                        include_config_blob=False,
                    )

                yield pm.ScanRequest(
                    component=component,
                    artefact=resource,
                    scan_content=iter_content(),
                    display_name=display_name,
                    target_product_id=target_product_id,
                    custom_metadata=component_artifact_metadata,
                )
            return
        elif (
            isinstance(resource_type, str)
            and resource_type == 'application/tar+vm-image-rootfs'
        ):
            # hardcoded semantics for vm-images:
            # merge all appropriate (tar)artifacts into one big tararchive
            component_artifact_metadata = protecode.util.component_artifact_metadata(
                component=component,
                artefact=resource,
                omit_component_version=False,
                omit_resource_version=False,
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
            raise NotImplementedError(resource_type)

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
        resource_group: tuple[cnudie.iter.ResourceNode],
        processing_mode: pm.ProcessingMode,
        known_scan_results: dict[str, tuple[pm.Product]],
        oci_client: oci.client.Client,
        s3_client: 'botocore.client.S3',
    ) -> typing.Iterator[pm.BDBA_ScanResult]:
        resource_node = resource_group[0]
        r = resource_node.resource
        c = resource_node.component
        group_name = f'{c.name}/{r.name}:{r.version} - {r.type}'
        logger.info(f'Processing {group_name}')

        products_to_import_from = tuple(self._products_with_relevant_triages(
            resource_group=resource_group,
        ))
        # todo: deduplicate/merge assessments
        component_vulnerabilities_with_assessments = tuple(
            self.iter_components_with_vulnerabilities_and_assessments(
                products_to_import_from=products_to_import_from,
            )
        )

        for scan_request in self.scan_requests(
          resource_group=resource_group,
          known_artifact_scans=known_scan_results,
          oci_client=oci_client,
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

          state = gcm.ScanState.FAILED if scan_failed else gcm.ScanState.SUCCEEDED
          component = scan_request.component
          artefact = scan_request.artefact

          if scan_failed:
            # pylint: disable=E1123
            yield pm.BDBA_ScanResult(
                component=component,
                artifact=artefact,
                status=pm.UploadStatus.DONE,
                result=scan_result,
                state=state,
            )
            return

          # scan succeeded
          logger.info(f'uploading package-version-hints for {scan_result.display_name()}')
          if version_hints := _package_version_hints(
            component=component,
            artefact=artefact,
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

          # pylint: disable=E1123
          yield pm.BDBA_ScanResult(
              component=component,
              artifact=artefact,
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
    resource_groups: typing.Iterable[tuple[cnudie.iter.ResourceNode]],
) -> dict[tuple[str, cm.ResourceIdentity, cm.ResourceType|str], pm.Product]:
    # This function populates a dict that contains all relevant scans for all artifact groups
    # in a given protecode group.
    # The created dict is later used to lookup existing scans when creating scan requests
    scan_results = dict()
    for resource_group in resource_groups:
        # groups are assumed to belong to same component + resource (name), and type so it is okay to
        # choose first one as representative
        component = resource_group[0].component
        resource = resource_group[0].resource
        resource_type = resource.type

        # special-handling for vm-image-rootfs - we keep different component-versions in parallel
        # for those
        if (
            isinstance(resource_type, str)
            and resource_type.startswith('application/tar+vm-image-rootfs')
        ):
            query_data = protecode.util.component_artifact_metadata(
                component=component,
                artefact=resource,
                omit_component_version=False,
                omit_resource_version=True,
            )
        else:
            query_data = protecode.util.component_artifact_metadata(
                component=component,
                artefact=resource,
                omit_component_version=True,
                omit_resource_version=True,
            )

        # TODO: since we ignore all versions for some of these artifact groups we potentially request
        # the same information multiple times. This is a quick hacked-in cache. Look into doing this
        # properly.
        # Note to self: adding LRU-Cache on classes is potentially a bad idea
        meta = frozenset([(k, v) for k,v in query_data.items()])
        scans = list(_proxy_list_apps(
            protecode_client=protecode_client,
            group_id=group_id,
            prototype_metadata=meta,
        ))

        resource_group_id = _resource_group_id(resource_group)
        scan_results[resource_group_id] = scans

    return scan_results


def _resource_groups(
    resource_nodes: typing.Generator[cnudie.iter.ResourceNode, None, None],
    filter_function: typing.Callable[[cnudie.iter.ResourceNode], bool],
    artefact_types=(
        cm.ResourceType.OCI_IMAGE,
        'application/tar+vm-image-rootfs',
    ),
) -> typing.Generator[tuple[cnudie.iter.ResourceNode], None, None]:
    '''
    group resources of same component name and resource version name

    this grouping is done in order to deduplicate identical resource versions shared between
    different component versions.
    '''

    # artefact-groups are grouped by:
    # component-name, resource-name, resource-version, resource-type
    # (thus implicitly deduplicating same resource-versions on different components)
    resource_groups: dict[tuple(str, cm.ResourceIdentity, str), list[cnudie.iter.ResourceNode]] \
        = collections.defaultdict(list)

    for resource_node in resource_nodes:
        if not resource_node.resource.type in artefact_types:
            continue

        if filter_function and not filter_function(resource_node):
            continue

        group_id = _resource_group_id((resource_node,))
        resource_groups[group_id].append(resource_node)

    logger.info(f'{len(resource_groups)=}')
    yield from (tuple(g) for g in resource_groups.values())


# TODO: Hacky cache. See _retrieve_existing_scan_results
@functools.lru_cache
def _proxy_list_apps(
    protecode_client: protecode.client.ProtecodeApi,
    group_id: int,
    prototype_metadata: typing.FrozenSet[typing.Tuple[str, str]],
):
    meta = {
        k: v
        for k,v in prototype_metadata
    }
    return list(protecode_client.list_apps(
            group_id=group_id,
            custom_attribs=meta,
        ))


def upload_grouped_images(
    protecode_api: protecode.client.ProtecodeApi,
    component: cm.Component|cm.ComponentDescriptor,
    protecode_group_id=5,
    parallel_jobs=8,
    cve_threshold=7,
    processing_mode=pm.ProcessingMode.RESCAN,
    filter_function: typing.Callable[[cnudie.iter.ResourceNode], bool]=(
        lambda node: True
    ),
    reference_group_ids=(),
    delivery_client=None,
    oci_client: oci.client.Client=None,
    s3_client: 'botocore.client.S3'=None,
) -> typing.Generator[pm.BDBA_ScanResult, None, None]:
    protecode_api.set_maximum_concurrent_connections(parallel_jobs)
    protecode_api.login()

    if isinstance(component, cm.ComponentDescriptor):
        component = component.component

    component_descriptor_lookup = cnudie.retrieve.create_default_component_descriptor_lookup(
        default_ctx_repo=component.current_repository_ctx(),
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
        resource_groups=groups
    )
    processor = ResourceGroupProcessor(
        group_id=protecode_group_id,
        reference_group_ids=reference_group_ids,
        cvss_threshold=cve_threshold,
        protecode_client=protecode_api,
    )

    def task_function(
        resource_group: tuple[cnudie.iter.ResourceNode],
        processing_mode: pm.ProcessingMode,
    ) -> tuple[pm.BDBA_ScanResult]:
        return tuple(processor.process(
            resource_group=resource_group,
            processing_mode=processing_mode,
            known_scan_results=known_scan_results,
            oci_client=oci_client,
            s3_client=s3_client,
        ))

    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel_jobs) as tpe:
        # queue one execution per artifact group
        futures = {
            tpe.submit(task_function, g, processing_mode)
            for g in groups
        }
        for completed_future in concurrent.futures.as_completed(futures):
            scan_results = completed_future.result()
            if delivery_client:
                protecode.util.upload_results_to_deliverydb(
                    delivery_client=delivery_client,
                    results=scan_results
                )
            else:
                logger.warning('Not uploading results to deliverydb, client not available')
            yield from scan_results
