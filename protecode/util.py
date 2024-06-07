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
import delivery.client
import dso.model
import gci.componentmodel as cm
import github.compliance.model as gcm
import github.compliance.report as gcr
import oci.client
import oci.model
import protecode.model as pm


logger = logging.getLogger(__name__)
ci.log.configure_default_logging(print_thread_id=True)


def iter_existing_findings(
    delivery_client: delivery.client.DeliveryServiceClient,
    resource_node: cnudie.iter.ResourceNode,
    finding_type: str | tuple[str],
    datasource: str=dso.model.Datasource.BDBA,
) -> collections.abc.Generator[dso.model.ArtefactMetadata, None, None]:
    artefact = dso.model.component_artefact_id_from_ocm(
        component=resource_node.component_id,
        artefact=resource_node.resource,
    )

    findings = delivery_client.query_metadata(
        components=[resource_node.component_id],
        type=finding_type,
    )

    return (
        finding for finding in findings
        if finding.meta.datasource == datasource and finding.artefact == artefact
    )


def iter_artefact_metadata(
    scanned_element: cnudie.iter.ResourceNode,
    scan_result: pm.AnalysisResult,
    license_cfg: image_scan.LicenseCfg=None,
    delivery_client: delivery.client.DeliveryServiceClient=None,
) -> collections.abc.Generator[dso.model.ArtefactMetadata, None, None]:
    now = datetime.datetime.now()
    discovery_date = datetime.date.today()
    datasource = dso.model.Datasource.BDBA

    artefact = gcm.artifact_from_node(node=scanned_element)
    artefact_ref = dso.model.component_artefact_id_from_ocm(
        component=scanned_element.component,
        artefact=artefact,
    )

    base_url = scan_result.base_url()
    report_url = scan_result.report_url()
    product_id = scan_result.product_id()
    group_id = scan_result.group_id()

    yield dso.model.artefact_scan_info(
        artefact_node=scanned_element,
        datasource=datasource,
        data={
            'report_url': report_url,
        },
    )

    findings: list[dso.model.ArtefactMetadata] = []
    for package in scan_result.components():
        package_name = package.name()
        package_version = package.version()

        filesystem_paths = list(iter_filesystem_paths(component=package))

        licenses = list({
            dso.model.License(
                name=license.name,
            ) for license in package.licenses
        })

        meta = dso.model.Metadata(
            datasource=datasource,
            type=dso.model.Datatype.STRUCTURE_INFO,
            creation_date=now,
        )

        structure_info = dso.model.StructureInfo(
            package_name=package_name,
            package_version=package_version,
            base_url=base_url,
            report_url=report_url,
            product_id=product_id,
            group_id=group_id,
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
            datasource=datasource,
            type=dso.model.Datatype.LICENSE,
            creation_date=now,
        )

        for license in licenses:
            if not license_cfg or license_cfg.is_allowed(license=license.name):
                continue

            license_finding = dso.model.LicenseFinding(
                package_name=package_name,
                package_version=package_version,
                base_url=base_url,
                report_url=report_url,
                product_id=product_id,
                group_id=group_id,
                severity=gcm.Severity.BLOCKER.name,
                license=license,
            )

            artefact_metadata = dso.model.ArtefactMetadata(
                artefact=artefact_ref,
                meta=meta,
                data=license_finding,
                discovery_date=discovery_date,
            )

            findings.append(artefact_metadata)
            yield artefact_metadata

        for vulnerability in package.vulnerabilities():
            if not vulnerability.cvss:
                # we only support vulnerabilities with a valid cvss v3 vector
                continue

            if vulnerability.historical():
                continue

            for triage in vulnerability.triages():
                meta = dso.model.Metadata(
                    datasource=datasource,
                    type=dso.model.Datatype.RESCORING,
                    creation_date=triage.modified.astimezone(datetime.UTC),
                )

                # We don't try to interpret the scope from BDBA here because we cannot completly
                # convert it in our scoping, so there would always be some edge cases where it does
                # not fit properly. However, by translating all triages for each scan result, we
                # implicitly use the BDBA scopes since they are already applied by BDBA to each scan
                # result. So, we can correctly mimic the BDBA scopes with the acceptable price of
                # redundant (rescoring) data.
                vulnerability_rescoring = dso.model.CustomRescoring(
                    finding=dso.model.RescoringVulnerabilityFinding(
                        package_name=package_name,
                        cve=vulnerability.cve(),
                    ),
                    referenced_type=dso.model.Datatype.VULNERABILITY,
                    severity=gcm.Severity.NONE.name, # bdba only allows triaging to NONE
                    user=dso.model.BDBAUser(
                        username=triage.user().get('username'),
                        email=triage.user().get('email'),
                        firstname=triage.user().get('firstname'),
                        lastname=triage.user().get('lastname'),
                    ),
                    matching_rules=[dso.model.MetaRescoringRules.BDBA_TRIAGE],
                    comment=triage.description(),
                )

                yield dso.model.ArtefactMetadata(
                    artefact=artefact_ref,
                    meta=meta,
                    data=vulnerability_rescoring,
                )

            meta = dso.model.Metadata(
                datasource=datasource,
                type=dso.model.Datatype.VULNERABILITY,
                creation_date=now,
            )

            vulnerability_finding = dso.model.VulnerabilityFinding(
                package_name=package_name,
                package_version=package_version,
                base_url=base_url,
                report_url=report_url,
                product_id=product_id,
                group_id=group_id,
                severity=gcr._criticality_classification(
                    cve_score=vulnerability.cve_severity(),
                ).name,
                cve=vulnerability.cve(),
                cvss_v3_score=vulnerability.cve_severity(),
                cvss=vulnerability.cvss,
                summary=vulnerability.summary(),
            )

            artefact_metadata = dso.model.ArtefactMetadata(
                artefact=artefact_ref,
                meta=meta,
                data=vulnerability_finding,
                discovery_date=discovery_date,
            )

            findings.append(artefact_metadata)
            yield artefact_metadata

    if delivery_client:
        # delete those BDBA findings which were found before for this scan but which are not part
        # of the current scan anymore -> those are either solved license findings or (now)
        # historical vulnerability findings (e.g. because a custom version was entered)
        existing_findings = iter_existing_findings(
            delivery_client=delivery_client,
            resource_node=scanned_element,
            finding_type=(
                dso.model.Datatype.VULNERABILITY,
                dso.model.Datatype.LICENSE,
            ),
        )

        stale_findings = []
        for existing_finding in existing_findings:
            for finding in findings:
                if (
                    existing_finding.meta.type == finding.meta.type
                    and existing_finding.data.key == finding.data.key
                ):
                    # finding still appeared in current scan result -> keep it
                    break
            else:
                # finding did not appear in current scan result -> delete it
                stale_findings.append(existing_finding)

        if stale_findings:
            delivery_client.delete_metadata(data=stale_findings)


def iter_filesystem_paths(
    component: pm.Component,
    file_type: str | None=None,
) -> collections.abc.Generator[dso.model.FilesystemPath, None, None]:
    for ext_obj in component.extended_objects():
        path = [
            dso.model.FilesystemPathEntry(
                path=path,
                type=type,
            ) for path_infos in ext_obj.raw.get('extended-fullpath', [])
            if (
                (path := path_infos.get('path')) and (type := path_infos.get('type'))
                and (not file_type or file_type == type)
            )
        ]

        yield dso.model.FilesystemPath(
            path=path,
            digest=ext_obj.sha1(),
        )


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
