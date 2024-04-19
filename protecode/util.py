# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import collections.abc
import datetime
import hashlib
import logging

import ci.log
import cnudie.iter
import concourse.model.traits.image_scan as image_scan
import dso.model
import gci.componentmodel as cm
import github.compliance.model as gcm
import github.compliance.report as gcr
import oci.client
import oci.model
import protecode.model as pm


logger = logging.getLogger(__name__)
ci.log.configure_default_logging(print_thread_id=True)


def iter_artefact_metadata(
    scanned_element: cnudie.iter.ResourceNode,
    scan_result: pm.AnalysisResult,
    license_cfg: image_scan.LicenseCfg=None,
) -> collections.abc.Generator[dso.model.ArtefactMetadata, None, None]:
    now = datetime.datetime.now()
    discovery_date = datetime.date.today()

    artefact = gcm.artifact_from_node(node=scanned_element)
    artefact_ref = dso.model.component_artefact_id_from_ocm(
        component=scanned_element.component,
        artefact=artefact,
    )

    for package in scan_result.components():
        package_id = dso.model.BDBAPackageId(
            package_name=package.name(),
            package_version=package.version(),
        )

        scan_id = dso.model.BDBAScanId(
            base_url=scan_result.base_url(),
            report_url=scan_result.report_url(),
            product_id=scan_result.product_id(),
            group_id=scan_result.group_id(),
        )

        filesystem_paths = list({
            dso.model.FilesystemPath(
                path=path,
                digest=digest,
            ) for path, digest in iter_filesystem_paths(component=package)
        })

        licenses = list({
            dso.model.License(
                name=license.name,
            ) for license in package.licenses
        })

        meta = dso.model.Metadata(
            datasource=dso.model.Datasource.BDBA,
            type=dso.model.Datatype.STRUCTURE_INFO,
            creation_date=now,
            last_update=now,
        )

        structure_info = dso.model.StructureInfo(
            id=package_id,
            scan_id=scan_id,
            licenses=licenses,
            filesystem_paths=filesystem_paths,
        )

        yield dso.model.ArtefactMetadata(
            artefact=artefact_ref,
            meta=meta,
            data=structure_info,
            discovery_date=discovery_date,
        )

        meta = dso.model.Metadata(
            datasource=dso.model.Datasource.BDBA,
            type=dso.model.Datatype.LICENSE,
            creation_date=now,
            last_update=now,
        )

        for license in licenses:
            if not license_cfg or license_cfg.is_allowed(license=license.name):
                continue

            license_finding = dso.model.LicenseFinding(
                id=package_id,
                scan_id=scan_id,
                severity=gcm.Severity.BLOCKER.name,
                license=license,
            )

            yield dso.model.ArtefactMetadata(
                artefact=artefact_ref,
                meta=meta,
                data=license_finding,
                discovery_date=discovery_date,
            )

        meta = dso.model.Metadata(
            datasource=dso.model.Datasource.BDBA,
            type=dso.model.Datatype.VULNERABILITY,
            creation_date=now,
            last_update=now,
        )

        for vulnerability in package.vulnerabilities():
            if (
                vulnerability.historical() or
                vulnerability.has_triage() or
                not vulnerability.cvss
            ):
                continue

            vulnerability_finding = dso.model.VulnerabilityFinding(
                id=package_id,
                scan_id=scan_id,
                severity=gcr._criticality_classification(
                    cve_score=vulnerability.cve_severity(),
                ).name,
                cve=vulnerability.cve(),
                cvss_v3_score=vulnerability.cve_severity(),
                cvss=vulnerability.cvss,
                summary=vulnerability.summary(),
            )

            yield dso.model.ArtefactMetadata(
                artefact=artefact_ref,
                meta=meta,
                data=vulnerability_finding,
                discovery_date=discovery_date,
            )


def iter_filesystem_paths(
    component: pm.Component,
    file_type: str | None = 'elf'
) -> collections.abc.Generator[tuple[str, str], None, None]:
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
) -> collections.abc.Generator[tuple[pm.Component, pm.Triage], None, None]:
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
    analysis_results: collections.abc.Iterable[pm.Product],
) -> int | None:
    # This is a helper function that is used when we create new ScanRequests for a given artifact
    # group. Since a given artifact group can trigger multiple scans in protecode, we want to be
    # able to find the correct one from a set of possible choices (if there is one).
    def filter_func(other_dict: dict[str, str]):
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
