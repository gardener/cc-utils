# SPDX-FileCopyrightText: 2021 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import dataclasses
import typing

import ci.util
import ctt.processing_model as pm
import ctt.util as ctt_util
import ocm
import oci.client
import oci.model as om

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
    resource: ocm.Resource,
    src_img_ref,
):
    original_ref_label = ocm.Label(
        name=original_ref_label_name,
        value=src_img_ref,
    )
    src_labels = resource.labels or []
    return ctt_util.add_label(
        src_labels=src_labels,
        label=original_ref_label,
    )


class PrefixUploader:
    def __init__(
        self,
        prefix,
        mangle=True,
        mangle_replacement_char='_',
        convert_to_relative_refs=False,
        remove_prefixes=[],
        **kwargs
    ):
        super().__init__(**kwargs)

        self._prefix = prefix
        self._mangle = mangle
        self._mangle_replacement_char = mangle_replacement_char
        self._convert_to_relative_refs = convert_to_relative_refs
        self._remove_prefixes = remove_prefixes

    def process(
        self,
        processing_job: pm.ProcessingJob,
        target_as_source=False,
    ):
        if processing_job.resource.access.type is not ocm.AccessType.OCI_REGISTRY:
            raise RuntimeError('PrefixUploader only supports access type == ociRegistry')

        if not target_as_source:
            src_ref = processing_job.resource.access.imageReference
        else:
            src_ref = processing_job.upload_request.target_ref

        src_ref = om.OciImageReference.to_image_ref(src_ref)
        src_base_ref = src_ref.ref_without_tag

        for remove_prefix in self._remove_prefixes:
            src_base_ref = src_base_ref.removeprefix(remove_prefix)

        if self._mangle:
            src_base_ref = src_base_ref.replace('.', self._mangle_replacement_char)

        # if a prefix is to be removed from existing src base ref, it is likely that it should be
        # replaced by the new prefix, instead of only prepended (where a joining `/` is reasonable).
        # Instead, leave it up to the configuration to decide on the joining character.
        if not self._remove_prefixes:
            tgt_ref = ci.util.urljoin(
                self._prefix,
                src_base_ref,
            )
        else:
            tgt_ref = self._prefix + src_base_ref

        if src_ref.has_mixed_tag:
            symbolical_tag, digest_tag = src_ref.parsed_mixed_tag
            tgt_ref = f'{tgt_ref}:{symbolical_tag}@{digest_tag}'
        elif src_ref.has_digest_tag:
            tgt_ref = f'{tgt_ref}@{src_ref.tag}'
        elif src_ref.has_symbolical_tag:
            tgt_ref = f'{tgt_ref}:{src_ref.tag}'

        if src_ref.has_digest_tag:
            processing_job.upload_request.reference_target_by_digest = True

        upload_request = dataclasses.replace(
            processing_job.upload_request,
            source_ref=processing_job.resource.access.imageReference,
            target_ref=tgt_ref,
        )

        if self._convert_to_relative_refs:
            # remove host from target ref
            # don't use artifact_path as self._prefix can also contain path elements
            relative_artifact_path = om.OciImageReference.to_image_ref(
                image_reference=tgt_ref,
                normalise=False, # don't inject docker special handlings
            ).local_ref
            access = ocm.RelativeOciAccess(
                reference=relative_artifact_path,
            )
        else:
            access = ocm.OciAccess(
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

    def process(
        self,
        processing_job: pm.ProcessingJob,
        target_as_source: bool=False,
    ):
        if processing_job.resource.access.type is not ocm.AccessType.OCI_REGISTRY:
            raise NotImplementedError

        if not target_as_source:
            src_ref = processing_job.resource.access.imageReference
        else:
            src_ref = processing_job.upload_request.target_ref

        src_ref = om.OciImageReference.to_image_ref(src_ref)

        if src_ref.has_digest_tag:
            raise RuntimeError('Cannot append tag suffix to resource that is accessed via digest')

        tgt_tag = self._separator.join((src_ref.tag, self._suffix))
        tgt_ref = ':'.join((src_ref.ref_without_tag, tgt_tag))

        upload_request = dataclasses.replace(
            processing_job.upload_request,
            source_ref=processing_job.resource.access.imageReference,
            target_ref=tgt_ref,
        )

        # propagate changed resource
        processing_job.processed_resource = dataclasses.replace(
            processing_job.resource,
            access=ocm.OciAccess(
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


class ExtraTagUploader:
    '''
    Uploader that will push additional (static) tags to uploaded images. Useful to e.g. add
    `latest` tag. Extra-Tags will be overwritten as a hardcoded behaviour of this uploader.
    '''
    def __init__(self, extra_tags: typing.Iterable[str]):
        self.extra_tags = tuple(extra_tags)

    def process(self, processing_job, target_as_source=False):
        processing_job.extra_tags = self.extra_tags

        return processing_job


class DigestUploader:
    '''
    sets `reference_target_by_digest` attribute in upload-request, which will result in
    target-component-descriptor's resouce's access use digest rather than tag to reference
    oci image. If `retain_symbolic_tag` is set, the symbolic tag is kept and the digest
    is appended, otherwise the digest overwrites the symbolic tag.
    '''
    def __init__(
        self,
        retain_symbolic_tag: bool=False,
    ):
        self._retain_symbolic_tag = retain_symbolic_tag

    def process(self, processing_job: pm.ProcessingJob, target_as_source=False):
        processing_job.upload_request.reference_target_by_digest = True
        processing_job.upload_request.retain_symbolic_tag = self._retain_symbolic_tag

        return processing_job
