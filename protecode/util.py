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

import collections
from concurrent.futures import ThreadPoolExecutor
import datetime
import logging
import functools
import tabulate
import typing

import ccc.delivery
import ccc.gcp
import ccc.protecode
import ci.log
import cnudie.retrieve
import cnudie.util
import dso.model
import gci.componentmodel as cm
import protecode.client
import protecode.model as pm
import protecode.scanning as ps
import model.protecode

logger = logging.getLogger(__name__)
ci.log.configure_default_logging(print_thread_id=True)


def upload_grouped_images(
    protecode_cfg: model.protecode.ProtecodeConfig | str,
    component_descriptor,
    protecode_group_id=5,
    parallel_jobs=8,
    cve_threshold=7,
    processing_mode=pm.ProcessingMode.RESCAN,
    image_filter_function: typing.Callable[[cm.Component, cm.Resource], bool]=(
        lambda component, resource: True
    ),
    tar_filter_function: typing.Callable[[cm.Component, cm.Resource], bool]=(
        lambda component, resource: True
    ),
    reference_group_ids=(),
) -> typing.Sequence[pm.BDBA_ScanResult]:
    protecode_api = ccc.protecode.client(protecode_cfg)
    protecode_api.set_maximum_concurrent_connections(parallel_jobs)
    protecode_api.login()
    groups = list(
        artifact_groups(
            component_descriptor=component_descriptor,
            image_filter_function=image_filter_function,
            tar_filter_function=tar_filter_function,
        )
    )
    # build lookup structure for existing scans
    known_results = _find_scan_results(
        protecode_client=protecode_api,
        group_id=protecode_group_id,
        artifact_groups=groups
    )
    processor = ps.ResourceGroupProcessor(
        group_id=protecode_group_id,
        scan_results=known_results,
        reference_group_ids=reference_group_ids,
        cvss_threshold=cve_threshold,
        protecode_client=protecode_api,
    )

    def task_function(
        artifact_group: pm.ArtifactGroup,
        processing_mode: pm.ProcessingMode,
    ) -> typing.Sequence[pm.BDBA_ScanResult]:
        return list(processor.process(
            artifact_group=artifact_group,
            processing_mode=processing_mode,
        ))

    with ThreadPoolExecutor(max_workers=parallel_jobs) as tpe:
        # queue one execution per artifact group
        futures = [
            tpe.submit(task_function, g, processing_mode)
            for g in groups
        ]
        # wait until all runs are finished and gather results
        results = tuple((
            result
            for future in futures
            for result in future.result()
        ))

    if (delivery_client := ccc.delivery.default_client_if_available()):
        logger.info('uploading results to deliverydb')
        try:
            for artefact_metadata in iter_artefact_metadata(results):
                delivery_client.upload_metadata(data=artefact_metadata)
        except:
            import traceback
            traceback.print_exc()
    else:
        logger.warning('not uploading results to deliverydb, client not available')

    return results


def filter_and_display_upload_results(
    upload_results: typing.Sequence[pm.BDBA_ScanResult],
    cve_threshold=7,
) -> typing.Sequence[pm.BDBA_ScanResult]:
    # we only require the analysis_results for now

    results_without_components = []
    results_below_cve_thresh = []
    results_above_cve_thresh = []

    for upload_result in upload_results:
        resource = upload_result.artifact

        if isinstance(upload_result, pm.BDBA_ScanResult):
            result = upload_result.result
        else:
            result = upload_result

        components = result.components()
        if not components:
            results_without_components.append(upload_result)
            continue

        greatest_cve = upload_result.greatest_cve_score

        if greatest_cve >= cve_threshold:
            try:
                # XXX HACK: just any image ref from group
                image_ref = resource.access.imageReference
                grafeas_client = ccc.gcp.GrafeasClient.for_image(image_ref)
                gcr_cve = -1
                for r in grafeas_client.filter_vulnerabilities(
                    image_ref,
                    cvss_threshold=cve_threshold,
                ):
                    gcr_cve = max(gcr_cve, r.vulnerability.cvssScore)
                # TODO: skip if < threshold - just report for now
            except Exception:
                import traceback
                logger.warning(
                    f'failed to retrieve vulnerabilies from gcr {traceback.format_exc()}'
                )

            results_above_cve_thresh.append(upload_result)
            continue
        else:
            results_below_cve_thresh.append(upload_result)
            continue

    if results_without_components:
        logger.warning(
            f'Protecode did not identify components for {len(results_without_components)=}:\n'
        )
        for result in results_without_components:
            print(result.result.display_name())
        print('')

    def render_results_table(upload_results: typing.Sequence[pm.BDBA_ScanResult]):
        header = ('Component Name', 'Greatest CVE')
        results = sorted(upload_results, key=lambda e: e.greatest_cve_score)

        def to_result(result):
            if isinstance(result, pm.BDBA_ScanResult):
                return result.result
            return result

        result = tabulate.tabulate(
            [(to_result(r).display_name(), r.greatest_cve_score) for r in results],
            headers=header,
            tablefmt='fancy_grid',
        )
        print(result)

    if results_below_cve_thresh:
        logger.info(f'The following components were below configured cve threshold {cve_threshold}')
        render_results_table(upload_results=results_below_cve_thresh)
        print('')

    if results_above_cve_thresh:
        logger.warning('The following components have critical vulnerabilities:')
        render_results_table(upload_results=results_above_cve_thresh)

    return results_above_cve_thresh, results_below_cve_thresh


def iter_artefact_metadata(
    results: typing.Collection[pm.BDBA_ScanResult],
) -> typing.Generator[dso.model.GreatestCVE, None, None]:
    for result in results:
        artefact_ref = dso.model.component_artefact_id_from_ocm(
            component=result.component,
            artefact=result.artifact,
        )
        meta = dso.model.Metadata(
            datasource=dso.model.Datasource.BDBA,
            type=dso.model.Datatype.VULNERABILITIES_AGGREGATED,
            creation_date=datetime.datetime.now()
        )
        cve = dso.model.GreatestCVE(
            greatestCvss3Score=result.greatest_cve_score,
            reportUrl=result.result.report_url()
        )
        yield dso.model.ArtefactMetadata(
            artefact=artefact_ref,
            meta=meta,
            data=cve,
        )

        meta = dso.model.Metadata(
            datasource=dso.model.Datasource.BDBA,
            type=dso.model.Datatype.LICENSES_AGGREGATED,
            creation_date=datetime.datetime.now()
        )
        license_names = list(dict.fromkeys(
            [
                component.license().name()
                for component in result.result.components()
                if component.license()
            ]
        ))
        license = dso.model.LicenseSummary(
            licenses=license_names,
        )
        yield dso.model.ArtefactMetadata(
            artefact=artefact_ref,
            meta=meta,
            data=license,
        )

        meta = dso.model.Metadata(
            datasource=dso.model.Datasource.BDBA,
            type=dso.model.Datatype.COMPONENTS,
            creation_date=datetime.datetime.now()
        )
        components = list(dict.fromkeys(
            [
                dso.model.ComponentVersion(
                    name=component.name(),
                    version=component.version(),
                )
                for component in result.result.components()
            ]
        ))
        component = dso.model.ComponentSummary(
            components=components
        )
        yield dso.model.ArtefactMetadata(
            artefact=artefact_ref,
            meta=meta,
            data=component,
        )


def corresponding_artifact_group_name(
    component: cm.Component,
    resource: cm.Resource,
):
    if isinstance(resource.access, cm.OciAccess):
        image_reference = resource.access.imageReference
        _, image_tag = image_reference.split(':')
        return (
            f'{resource.name}_{image_tag}_{component.name}'.replace('/', '_')
        )

    return (
        f'{resource.name}_{resource.version}_{component.name}'.replace('/', '_')
    )


def _update_artifact_groups(
    component: cm.Component,
    resource: cm.Resource,
    artifact_groups: typing.Dict[str, pm.ArtifactGroup],
    artifact_group_ctor: typing.Callable[[str], pm.ArtifactGroup],
    filter: typing.Callable[[cm.Component, cm.Resource], bool] | None = None,
):
    if filter and not filter(component, resource):
        return

    name = corresponding_artifact_group_name(component, resource)

    if name not in artifact_groups:
        artifact_groups[name] = artifact_group_ctor(name)

    artifact_groups[name].component_artifacts.append(
        pm.ComponentArtifact(component, resource)
    )


def artifact_groups(
    component_descriptor: cm.ComponentDescriptor,
    image_filter_function: typing.Callable[[cm.Component, cm.Resource], bool],
    tar_filter_function: typing.Callable[[cm.Component, cm.Resource], bool],
) -> typing.Iterator[pm.ArtifactGroup]:
    '''Build artifact groups from the given component-descriptor
    '''
    components = list(cnudie.retrieve.components(component=component_descriptor))
    artifact_groups: typing.Dict[str, pm.ArtifactGroup] = dict()
    for component in components:
        for resource in component.resources:

            if resource.type not in [
                cm.ResourceType.OCI_IMAGE,
                'application/tar+vm-image-rootfs',
            ]:
                continue

            match resource.access:
                case cm.OciAccess():
                    _update_artifact_groups(
                        component=component,
                        resource=resource,
                        artifact_group_ctor=pm.OciArtifactGroup,
                        filter=image_filter_function,
                        artifact_groups=artifact_groups,
                    )
                case cm.S3Access():
                    _update_artifact_groups(
                        component=component,
                        resource=resource,
                        artifact_group_ctor=pm.TarRootfsArtifactGroup,
                        filter=tar_filter_function,
                        artifact_groups=artifact_groups,
                    )
    logger.info(f'Built {len(artifact_groups.values())} artifact groups')
    yield from artifact_groups.values()


def enum_triages(
    result: pm.AnalysisResult
) -> typing.Iterator[typing.Tuple[pm.Component, pm.Triage]]:
    for component in result.components():
        for vulnerability in component.vulnerabilities():
            for triage in vulnerability.triages():
                yield component, triage


def enum_component_versions(
    scan_result: pm.AnalysisResult,
    component_name: str,
) -> typing.Iterator[str]:
    for component in scan_result.components():
        if component.name() == component_name:
            yield component.version()


def wait_for_scans_to_finish(
    scans: typing.Iterable[pm.AnalysisResult],
    protecode_api: protecode.client.ProtecodeApi
) -> typing.Generator[pm.AnalysisResult, None, None]:
    for scan in scans:
        yield wait_for_scan_to_finish(scan, protecode_api)
        logger.info(f'finished waiting for {scan.product_id()}')


def wait_for_scan_to_finish(
    scan: pm.AnalysisResult,
    protecode_api: protecode.client.ProtecodeApi,
) -> pm.AnalysisResult:
    product_id = scan.product_id()
    logger.info(f'waiting for {product_id}')
    return protecode_api.wait_for_scan_result(product_id)


@functools.lru_cache
def _image_digest(image_reference: str) -> str:
    oci_client = ccc.oci.oci_client()
    return oci_client.to_digest_hash(
        image_reference=image_reference,
    )


def component_artifact_metadata(
    component_artifact: pm.ComponentArtifact,
    omit_component_version: bool,
    omit_resource_version: bool,
):
    '''Build a metadata-dict for the given ComponentArtifact.

    The resulting dict is usually referred to as "Custom data" by Protecode and is used to filter
    results when searching.
    '''
    metadata = {
        'COMPONENT_NAME': component_artifact.component.name,
    }
    if not omit_component_version:
        metadata.update({
            'COMPONENT_VERSION': component_artifact.component.version,
        })
    match component_artifact.artifact.access:
        case cm.OciAccess():
            metadata['IMAGE_REFERENCE_NAME'] = component_artifact.artifact.name
            metadata['RESOURCE_TYPE'] = 'ociImage'
            if not omit_resource_version:
                img_ref_with_digest = _image_digest(
                    image_reference=component_artifact.artifact.access.imageReference
                )
                digest = img_ref_with_digest.split('@')[-1]
                metadata['IMAGE_REFERENCE'] = component_artifact.artifact.access.imageReference
                metadata['IMAGE_VERSION'] = component_artifact.artifact.version
                metadata['IMAGE_DIGEST'] = digest
                metadata['DIGEST_IMAGE_REFERENCE'] = str(img_ref_with_digest)
        case cm.S3Access():
            metadata['RESOURCE_TYPE'] = 'application/tar+vm-image-rootfs'
            if not omit_resource_version:
                metadata['IMAGE_VERSION'] = component_artifact.artifact.version
        case _:
            raise NotImplementedError(component_artifact.artifact.access)

    return metadata


def _matching_analysis_result_id(
    component_artifact_metadata: dict[str, str],
    analysis_results: typing.Iterable[pm.Product],
) -> int | None:
    # This is a helper function that is used when we create new ScanRequests for a given artifact
    # group. Since a given artifact group can trigger multiple scans in protecode, we want to be
    # able to find the correct one from a set of possible choices (if there is one).
    def filter_func(other_dict: typing.Dict[str, str]):
        # filter-function to find the correct match. We consider a given dict a match if
        # it contains all keys we have and the values associated with these keys are identical.
        # Note: That means that (manually) added protecode-metadata will not interfere.
        for key in component_artifact_metadata:
            if key not in other_dict.keys():
                return False
            if other_dict[key] != component_artifact_metadata[key]:
                return False
        return True

    filtered_results = (
            r for r in analysis_results
            if filter_func(r.custom_data())
        )
    result = next(filtered_results, None)

    # There should be at most one possible candidate
    if next_result := next(filtered_results, None):
        raise RuntimeError(
            'More than one scan result found for component artifact. '
            f'Found {result} and {next_result} - aborting, but there might be more. Please check '
            'for additional protecode scans with identical custom data'
        )

    if result:
        return result.product_id()
    else:
        return None


# TODO: Hacky cache. See _find_scan_results
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


def _find_scan_results(
    protecode_client: protecode.client.ProtecodeApi,
    group_id: int,
    artifact_groups: typing.Iterable[pm.ArtifactGroup],
) -> typing.Dict[str, pm.Product]:
    # This function populates a dict that contains all relevant scans for all artifact groups
    # in a given protecode group.
    # The created dict is later used to lookup existing scans when creating scan requests
    scan_results = dict()
    for artifact_group in artifact_groups:
        match artifact_group:
            case pm.OciArtifactGroup():
                # prepare prototypical metadata for the artifact group, i.e. without any version
                # information
                prototype_metadata = component_artifact_metadata(
                    component_artifact=artifact_group.component_artifacts[0],
                    omit_component_version=True,
                    omit_resource_version=True,
                )
            case pm.TarRootfsArtifactGroup():
                prototype_metadata = component_artifact_metadata(
                    component_artifact=artifact_group.component_artifacts[0],
                    omit_component_version=False,
                    omit_resource_version=True,
                )
        # TODO: since we ignore all versions for some of these artifact groups we potentially request
        # the same information multiple times. This is a quick hacked-in cache. Look into doing this
        # properly.
        # Note to self: adding LRU-Cache on classes is potentially a bad idea
        meta = frozenset([(k, v) for k,v in prototype_metadata.items()])
        scans = list(_proxy_list_apps(
            protecode_client=protecode_client,
            group_id=group_id,
            prototype_metadata=meta,
        ))
        scan_results[artifact_group.name] = scans

    return scan_results


def copy_triages(
    from_results: typing.Iterable[pm.AnalysisResult],
    to_results: typing.Iterable[pm.AnalysisResult],
    to_group_id: int,
    protecode_api: protecode.client.ProtecodeApi,
):
    '''Copy triages from a number of source scans to several target scans.

    Triages are deduplicated when copying. Also, triages will only be imported if the triaged
    component is present on the target and the triage isn't already in place.
    '''
    # helper function for logging
    def _len(d: typing.Dict) -> int:
        result = 0
        for key in d.keys():
            match d[key]:
                case dict():
                    result += _len(d[key])
                case set():
                    result += len(d[key])
        return result

    from_triages = collections.defaultdict(lambda: collections.defaultdict(set))
    to_triages = collections.defaultdict(lambda: collections.defaultdict(set))

    # prepare datastructure for all known triages
    for from_result in from_results:
        for component, triage in enum_triages(from_result):
            from_triages[component.name()][component.version()].add(triage)

    to_ids = {p.product_id() for p in to_results}
    if not (source_triages := _len(from_triages)):
        from_ids = {p.product_id() for p in from_results}
        logger.debug(f'No triages to transport from {from_ids} to {to_ids}')
        return

    logger.info(f'Found {source_triages} triages to import to products {to_ids}')

    for to_result in to_results:
        to_result_id = to_result.product_id()
        to_result_name = to_result.display_name()
        for component, triage in enum_triages(to_result):
            to_triages[component.name()][component.version()].add(triage)

        logger.debug(
            f'{_len(to_triages)} already present on {to_result_id} ({to_result_name})'
        )

        to_component_versions = {
            component.name(): list(enum_component_versions(to_result, component.name()))
            for component in to_result.components()
        }

        # apply triages
        # - if the component is present in the given version AND
        # - if the triage is not already present for this version
        for component_name in from_triages.keys():
            for component_version in from_triages[component_name].keys():
                for triage in from_triages[component_name][component_version]:
                    if not component_name in to_component_versions.keys():
                        # the target scan result does not have a component with the same name
                        logger.debug(
                            f'Skipping triage for {component_name} as component is not present '
                            f'on {to_result_id} ({to_result_name})'
                        )
                        continue

                    for to_component_version in  to_component_versions[component_name]:
                        if triage in to_triages[component_name][component_version]:
                            # the triage is already present for this component and version
                            logger.debug(
                                f'Skipping triage {triage} for {component_name} in version '
                                f'{component_version} as it is already present '
                                f'on {to_result_id} ({to_result_name})'
                            )
                            continue

                        logger.debug(
                            f'Adding triage {triage} to {component_name} in version '
                            f'{component_version} to {to_result_id} ({to_result_name})'
                        )
                        protecode_api.add_triage(
                            triage=triage,
                            product_id=to_result.product_id(),
                            group_id=to_group_id,
                            component_version=to_component_version,
                        )


def auto_triage(
    analysis_result: pm.AnalysisResult,
    cvss_threshold: float,
    protecode_api: protecode.client.ProtecodeApi,
):
    '''Automatically triage all current vulnerabilities below the given CVSS-threshold on the given
    Protecode scan.

    Components with matching vulnerabilities will be assigned an arbitrary version
    (`[ci]-auto-triage`) since a version is required by Protecode to be able to triage.
    '''
    product_id = analysis_result.product_id()
    product_name = analysis_result.name()

    for component in analysis_result.components():
        component_version = component.version()
        for vulnerability in component.vulnerabilities():
            if (
                vulnerability.cve_severity() >= cvss_threshold and not vulnerability.historical()
                and not vulnerability.has_triage()
            ):
                # component version needs to be set to triage. If we actually have a vulnerability
                # we want to auto-triage we need to set the version first.
                component_name = component.name()
                vulnerability_cve = vulnerability.cve()
                if not component_version:
                    component_version = '[ci]-auto-triage'
                    protecode_api.set_component_version(
                        component_name=component_name,
                        component_version=component_version,
                        scope=pm.VersionOverrideScope.APP,
                        objects=list(o.sha1() for o in component.extended_objects()),
                        app_id=product_id,
                    )

                triage_dict = {
                    'component': component_name,
                    'version': component_version,
                    'vulns': [vulnerability_cve],
                    'scope': pm.TriageScope.RESULT.value,
                    'reason': 'OT', # "other"
                    'description': 'Auto-generated due to skip-scan label',
                    'product_id': product_id,
                }
                logger.debug(
                    f'Auto-triaging {vulnerability_cve} for {component_name} '
                    f'in product {product_id} ({product_name})'
                )
                protecode_api.add_triage_raw(
                    triage_dict=triage_dict,
                )
