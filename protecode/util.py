# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import datetime
import hashlib
import logging
import time
import typing

import ci.log
import delivery.client
import dso.model
import gci.componentmodel as cm
import github.compliance.model
import oci.client
import oci.model
import protecode.model as pm


logger = logging.getLogger(__name__)
ci.log.configure_default_logging(print_thread_id=True)


def sync_results_with_delivery_db(
    delivery_client: delivery.client.DeliveryServiceClient,
    results: typing.Iterable[pm.BDBAScanResult],
    bdba_cfg_name: str,
    max_retries: int=3,
    retry_count: int=0,
):
    try:
        # Delete vulnerabilites with new triages from delivery-db for now
        # XXX in the future, implement own triage object in delivery-db
        delivery_client.update_metadata(
            data=iter_artefact_metadata(
                results=results,
                bdba_cfg_name=bdba_cfg_name,
            ),
        )
    except:
        import traceback
        traceback.print_exc()
        if retry_count < max_retries:
            retry_interval = (retry_count + 1) * 10
            logger.warning(
                f'caught error while updating delivery-db, will retry in {retry_interval} s'
            )
            time.sleep(retry_interval)
            sync_results_with_delivery_db(
                delivery_client=delivery_client,
                results=results,
                bdba_cfg_name=bdba_cfg_name,
                max_retries=max_retries,
                retry_count=retry_count + 1,
            )


def iter_artefact_metadata(
    results: typing.Collection[pm.BDBAScanResult],
    bdba_cfg_name: str,
) -> typing.Generator[dso.model.ArtefactMetadata, None, None]:
    seen_product_ids = set()
    for result in results:
        artefact = github.compliance.model.artifact_from_node(result.scanned_element)
        artefact_ref = dso.model.component_artefact_id_from_ocm(
            component=result.scanned_element.component,
            artefact=artefact,
        )

        if result.result.product_id() not in seen_product_ids:
            seen_product_ids.add(result.result.product_id())
            # yield dummy vulnerability finding to delete all remaining findings
            # in case all findings have been triaged
            meta = dso.model.Metadata(
                datasource=dso.model.Datasource.BDBA,
                type=dso.model.Datatype.VULNERABILITIES_CVE,
                creation_date=datetime.datetime.now()
            )

            cve = dso.model.CVE(
                cve=None,
                cvss3Score=-1, # hardcoded assumption, -1 means finding was triaged
                cvss=None,
                affected_package_name=None,
                affected_package_version=None,
                reportUrl=result.result.report_url(),
                product_id=result.result.product_id(),
                group_id=result.result.group_id(),
                base_url=result.result.base_url(),
                bdba_cfg_name=bdba_cfg_name,
            )

            yield dso.model.ArtefactMetadata(
                artefact=artefact_ref,
                meta=meta,
                data=cve,
            )

        if isinstance(result, pm.VulnerabilityScanResult):
            result: pm.VulnerabilityScanResult

            meta = dso.model.Metadata(
                datasource=dso.model.Datasource.BDBA,
                type=dso.model.Datatype.VULNERABILITIES_CVE,
                creation_date=datetime.datetime.now()
            )

            if not result.vulnerability.cvss:
                # no cvss3_vector specified -> ignore cvss2_vector
                continue

            cve = dso.model.CVE(
                cve=result.vulnerability.cve(),
                cvss3Score=result.vulnerability.cve_severity(),
                cvss=result.vulnerability.cvss,
                affected_package_name=result.affected_package.name(),
                affected_package_version=result.affected_package.version(),
                reportUrl=result.result.report_url(),
                product_id=result.result.product_id(),
                group_id=result.result.group_id(),
                base_url=result.result.base_url(),
                bdba_cfg_name=bdba_cfg_name,
            )

            yield dso.model.ArtefactMetadata(
                artefact=artefact_ref,
                discovery_date=result.discovery_date,
                meta=meta,
                data=cve,
            )
        elif isinstance(result, pm.LicenseScanResult):
            result: pm.LicenseScanResult

            meta = dso.model.Metadata(
                datasource=dso.model.Datasource.BDBA,
                type=dso.model.Datatype.LICENSE,
                creation_date=datetime.datetime.now()
            )

            license = dso.model.License(
                name=result.license.name,
                reportUrl=result.result.report_url(),
                productId=result.result.product_id(),
            )

            yield dso.model.ArtefactMetadata(
                artefact=artefact_ref,
                discovery_date=result.discovery_date,
                meta=meta,
                data=license,
            )
        elif isinstance(result, pm.ComponentsScanResult):
            result: pm.ComponentsScanResult

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
            components = dso.model.ComponentSummary(
                components=components
            )

            yield dso.model.ArtefactMetadata(
                artefact=artefact_ref,
                discovery_date=result.discovery_date,
                meta=meta,
                data=components,
            )

            meta = dso.model.Metadata(
                datasource=dso.model.Datasource.BDBA,
                type=dso.model.Datatype.FILESYSTEM_PATHS,
                creation_date=datetime.datetime.now()
            )

            # avoid duplicates
            filesystem_paths = set(
                dso.model.FilesystemPath(
                    path=path,
                    digest=digest,
                )
                for component in result.result.components()
                for path, digest in iter_filesystem_paths(component=component)
            )
            filesystem_paths = dso.model.FilesystemPaths(
                paths=list(filesystem_paths),
            )

            yield dso.model.ArtefactMetadata(
                artefact=artefact_ref,
                discovery_date=result.discovery_date,
                meta=meta,
                data=filesystem_paths,
            )
        else:
            raise NotImplementedError(f'processing of result with type {type(result)} not supported')


def iter_filesystem_paths(
    component: pm.Component,
    file_type: str | None = 'elf'
) -> typing.Generator[tuple[str, str], None, None]:
    for ext_obj in component.extended_objects():
        for path_infos in ext_obj.raw.get('extended-fullpath', []):

            # be defensive, dont break
            if not (fullpath := path_infos.get('path')):
                continue
            if not (path_info_type := path_infos.get('type')):
                continue

            if not file_type:
                yield fullpath, ext_obj.sha1()

            if path_info_type == file_type:
                yield fullpath, ext_obj.sha1()


def enum_triages(
    result: pm.AnalysisResult,
) -> typing.Iterator[typing.Tuple[pm.Component, pm.Triage]]:
    for component in result.components():
        for vulnerability in component.vulnerabilities():
            for triage in vulnerability.triages():
                yield component, triage


def component_artifact_metadata(
    component: cm.Component,
    artefact: cm.Artifact,
    omit_component_version: bool,
    omit_resource_version: bool,
    oci_client: oci.client.Client,
):
    ''' returns a dict for querying bdba scan results (use for custom-data query)
    '''
    metadata = {'COMPONENT_NAME': component.name}

    if not omit_component_version:
        metadata |= {'COMPONENT_VERSION': component.version}

    if isinstance(artefact.access, cm.OciAccess):
        metadata['IMAGE_REFERENCE_NAME'] = artefact.name
        metadata['RESOURCE_TYPE'] = 'ociImage'
        if not omit_resource_version:
            image_reference = image_ref_with_digest(
                image_reference=artefact.access.imageReference,
                digest=artefact.digest,
                oci_client=oci_client,
            )
            metadata['IMAGE_REFERENCE'] = image_reference
            metadata['IMAGE_VERSION'] = artefact.version
    elif isinstance(artefact.access, cm.S3Access):
        metadata['RESOURCE_TYPE'] = 'application/tar+vm-image-rootfs'
        if not omit_resource_version:
            metadata['IMAGE_VERSION'] = artefact.version
    else:
        raise NotImplementedError(artefact.access)

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

    filtered_results = tuple(r for r in analysis_results if filter_func(r.custom_data()))

    if not filtered_results:
        return None

    # there may be multiple possible candidates since we switched from including the component
    # version in the component artefact metadata to excluding it
    if len(filtered_results) > 1:
        logger.warning(
            'more than one scan result found for component artefact with '
            f'{component_artifact_metadata=}, will use latest scan result...'
        )
        filtered_results = sorted(
            filtered_results,
            key=lambda result: result.product_id(),
            reverse=True,
        )

    # there is at least one result and they are ordered (latest product id first)
    return filtered_results[0].product_id()


def image_ref_with_digest(
    image_reference: str | oci.model.OciImageReference,
    digest: cm.DigestSpec,
    oci_client: oci.client.Client,
) -> str:
    image_reference = oci.model.OciImageReference.to_image_ref(image_reference=image_reference)

    if image_reference.has_digest_tag:
        return image_reference.original_image_reference

    if not (digest and digest.value):
        digest = cm.DigestSpec(
            hashAlgorithm=None,
            normalisationAlgorithm=None,
            value=hashlib.sha256(oci_client.manifest_raw(
                image_reference=image_reference,
                accept=oci.model.MimeTypes.prefer_multiarch,
            ).content).hexdigest(),
        )

    return image_reference.with_tag(tag=digest.oci_tag)
