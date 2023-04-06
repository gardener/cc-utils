# SPDX-FileCopyrightText: 2021 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import dataclasses

import ci.util
import gci.componentmodel as cm
import oci.client

import ctt.processing_model as pm
import ctt.util as ctt_util

original_ref_label_name = 'cloud.gardener.cnudie/migration/original_ref'


class IdentityUploader:
    def process(self, processing_job, target_as_source=False):
        upload_request = processing_job.upload_request

        _, _, src_tag = oci.client._split_image_reference(upload_request.source_ref)
        if ':' in src_tag:
            raise NotImplementedError

        if not target_as_source:
            upload_request = dataclasses.replace(
                processing_job.upload_request,
                target_ref=processing_job.upload_request.source_ref,
            )

        return dataclasses.replace(
            processing_job,
            upload_request=upload_request,
        )


def labels_with_migration_hint(
    resource: cm.Resource,
    src_img_ref,
):
    original_ref_label = cm.Label(
        name=original_ref_label_name,
        value=src_img_ref,
    )
    src_labels = resource.labels or []
    return ctt_util.add_label(
        src_labels=src_labels,
        label=original_ref_label,
    )


def calc_tgt_tag(src_tag: str) -> str:
    # if the source resource is referenced via hash digest, we (1) have no symbolic
    # tag and (2) cannot guarantee that the hash digest of the target stays the same
    # (e.g. depends on resource filtering). Therefore we cannot simply upload the
    # resource under the same hash digest. As a workaround, we tag these resources
    # with the 'latest' tag by default. After the resource upload, the digest is
    # returned in the registry response and gets written to the component descriptor
    if ':' in src_tag:
        return 'latest'
    else:
        return src_tag


class PrefixUploader:
    def __init__(
        self,
        prefix,
        mangle=True,
        convert_to_relative_refs=False,
        **kwargs
    ):
        super().__init__(**kwargs)

        self._prefix = prefix
        self._mangle = mangle
        self._convert_to_relative_refs = convert_to_relative_refs

    def process(
        self,
        processing_job: pm.ProcessingJob,
        target_as_source=False,
    ):
        if processing_job.resource.access.type is not cm.AccessType.OCI_REGISTRY:
            raise RuntimeError('PrefixUploader only supports access type == ociRegistry')

        if not target_as_source:
            src_ref = processing_job.resource.access.imageReference
        else:
            src_ref = processing_job.upload_request.target_ref

        src_prefix, src_name, src_tag = oci.client._split_image_reference(src_ref)

        artifact_path = ci.util.urljoin(src_prefix, src_name)
        if self._mangle:
            artifact_path = artifact_path.replace('.', '_')

        tgt_tag = calc_tgt_tag(src_tag)
        artifact_path = ':'.join((artifact_path, tgt_tag))
        tgt_ref = ci.util.urljoin(self._prefix, artifact_path)

        upload_request = dataclasses.replace(
            processing_job.upload_request,
            source_ref=processing_job.resource.access.imageReference,
            target_ref=tgt_ref,
        )

        if self._convert_to_relative_refs:
            # remove host from target ref
            # don't use artifact_path as self._prefix can also contain path elements
            relative_artifact_path = '/'.join(tgt_ref.split("/")[1:])
            access = cm.RelativeOciAccess(
                reference=relative_artifact_path
            )
        else:
            access = cm.OciAccess(
                imageReference=tgt_ref,
            )

        # propagate changed resource
        processing_job.processed_resource = dataclasses.replace(
            processing_job.resource,
            access=access,
            labels=labels_with_migration_hint(
                resource=processing_job.resource,
                src_img_ref=processing_job.resource.access.imageReference,
            ),
        )

        return dataclasses.replace(
            processing_job,
            upload_request=upload_request
        )


class TagSuffixUploader:
    def __init__(
        self,
        suffix,
        separator='-',
    ):
        self._suffix = suffix
        self._separator = separator

    def process(self, processing_job, target_as_source=False):
        if processing_job.resource.type is not cm.ResourceType.OCI_IMAGE:
            raise NotImplementedError

        if not target_as_source:
            src_ref = processing_job.resource.access.imageReference
        else:
            src_ref = processing_job.upload_request.target_ref

        src_prefix, src_name, src_tag = oci.client._split_image_reference(src_ref)

        if ':' in src_tag:
            raise RuntimeError('Cannot append tag suffix to resource that is accessed via digest')

        src_name = ci.util.urljoin(src_prefix, src_name)
        tgt_tag = self._separator.join((src_tag, self._suffix))
        tgt_ref = ':'.join((src_name, tgt_tag))

        upload_request = dataclasses.replace(
            processing_job.upload_request,
            source_ref=processing_job.resource.access.imageReference,
            target_ref=tgt_ref,
        )

        # propagate changed resource
        processing_job.processed_resource = dataclasses.replace(
            processing_job.resource,
            access=cm.OciAccess(
                imageReference=tgt_ref,
            ),
            labels=labels_with_migration_hint(
                resource=processing_job.resource,
                src_img_ref=processing_job.resource.access.imageReference,
            ),
        )

        return dataclasses.replace(
            processing_job,
            upload_request=upload_request
        )


class RBSCCustomerFacingRepoLoader:
    def __init__(
        self,
        src_ctx_repo_url,
        tgt_ctx_repo_url,
        **kwargs
    ):
        super().__init__(**kwargs)

        self._src_ctx_repo_url = src_ctx_repo_url
        self._tgt_ctx_repo_url = tgt_ctx_repo_url

    def process(
        self,
        processing_job: pm.ProcessingJob,
        target_as_source=False,
    ):
        resource = processing_job.resource

        if processing_job.resource.access.type is not cm.AccessType.RELATIVE_OCI_REFERENCE:
            raise RuntimeError(
                'RBSCCustomerFacingRepoLoader only support access type == relativeOciReference'
            )

        src_ref = ci.util.urljoin(self._src_ctx_repo_url, resource.access.reference)
        _, src_name, src_tag = oci.client._split_image_reference(src_ref)

        tgt_tag = calc_tgt_tag(src_tag)
        artifact_path = ':'.join([src_name, tgt_tag])
        tgt_ref = ci.util.urljoin(self._tgt_ctx_repo_url, artifact_path)

        upload_request = dataclasses.replace(
            processing_job.upload_request,
            source_ref=src_ref,
            target_ref=tgt_ref,
        )

        # propagate changed resource
        processing_job.processed_resource = dataclasses.replace(
            processing_job.resource,
            access=cm.OciAccess(
                imageReference=tgt_ref,
            ),
            labels=labels_with_migration_hint(
                resource=processing_job.resource,
                src_img_ref=src_ref,
            ),
        )

        return dataclasses.replace(
            processing_job,
            upload_request=upload_request
        )
