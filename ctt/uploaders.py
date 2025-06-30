# SPDX-FileCopyrightText: 2021 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import abc
import collections.abc

import ci.util
import ctt.model
import ctt.util as ctt_util
import ocm

original_ref_label_name = 'cloud.gardener.cnudie/migration/original_ref'


def labels_with_migration_hint(
    resource: ocm.Resource,
    src_img_ref: str,
) -> collections.abc.Sequence[ocm.Label]:
    original_ref_label = ocm.Label(
        name=original_ref_label_name,
        value=src_img_ref,
    )
    src_labels = resource.labels or []
    return ctt_util.add_label(
        src_labels=src_labels,
        label=original_ref_label,
    )


class UploaderBase:
    @abc.abstractmethod
    def process(
        self,
        replication_resource_element: ctt.model.ReplicationResourceElement,
        /,
        **kwargs,
    ) -> ctt.model.ReplicationResourceElement:
        raise NotImplementedError('must be implemented by its subclasses')


class RepositoryUploader(UploaderBase):
    def __init__(
        self,
        repository: str,
        mangle: bool=True,
        mangle_replacement_char: str='_',
        convert_to_relative_refs: bool=False,
        remove_prefixes: list[str]=[],
    ):
        self._repository = repository
        self._mangle = mangle
        self._mangle_replacement_char = mangle_replacement_char
        self._convert_to_relative_refs = convert_to_relative_refs
        self._remove_prefixes = remove_prefixes

    def process(
        self,
        replication_resource_element: ctt.model.ReplicationResourceElement,
        /,
        tgt_oci_registry: str,
        target_as_source: bool=False,
    ) -> ctt.model.ReplicationResourceElement:
        if replication_resource_element.source.access.type is not ocm.AccessType.OCI_REGISTRY:
            raise RuntimeError(f'RepositoryUploader only supports {ocm.AccessType.OCI_REGISTRY=}')

        if not target_as_source:
            src_ref = replication_resource_element.src_ref
        else:
            src_ref = replication_resource_element.tgt_ref

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
                tgt_oci_registry,
                self._repository,
                src_base_ref,
            )
        else:
            tgt_ref = ci.util.urljoin(tgt_oci_registry, self._repository) + src_base_ref

        if src_ref.has_mixed_tag:
            symbolical_tag, digest_tag = src_ref.parsed_mixed_tag
            tgt_ref = f'{tgt_ref}:{symbolical_tag}@{digest_tag}'
        elif src_ref.has_digest_tag:
            tgt_ref = f'{tgt_ref}@{src_ref.tag}'
        elif src_ref.has_symbolical_tag:
            tgt_ref = f'{tgt_ref}:{src_ref.tag}'

        if src_ref.has_digest_tag:
            replication_resource_element.reference_by_digest = True

        if self._convert_to_relative_refs:
            replication_resource_element.convert_to_relative_ref = True

        replication_resource_element.target.access = ocm.OciAccess(
            imageReference=tgt_ref,
        )

        replication_resource_element.target.labels = labels_with_migration_hint(
            resource=replication_resource_element.target,
            src_img_ref=str(replication_resource_element.src_ref),
        )

        return replication_resource_element


class TagSuffixUploader(UploaderBase):
    def __init__(
        self,
        suffix,
        separator='-',
    ):
        self._suffix = suffix
        self._separator = separator

    def process(
        self,
        replication_resource_element: ctt.model.ReplicationResourceElement,
        /,
        target_as_source: bool=False,
        **kwargs,
    ) -> ctt.model.ReplicationResourceElement:
        if replication_resource_element.source.access.type is not ocm.AccessType.OCI_REGISTRY:
            raise RuntimeError(f'TagSuffixUploader only supports {ocm.AccessType.OCI_REGISTRY=}')

        if not target_as_source:
            src_ref = replication_resource_element.src_ref
        else:
            src_ref = replication_resource_element.tgt_ref

        if src_ref.has_digest_tag:
            raise RuntimeError('Cannot append tag suffix to resource that is accessed via digest')

        tgt_tag = self._separator.join((src_ref.tag, self._suffix))
        tgt_ref = ':'.join((src_ref.ref_without_tag, tgt_tag))

        replication_resource_element.target.access = ocm.OciAccess(
            imageReference=tgt_ref,
        )

        replication_resource_element.target.labels = labels_with_migration_hint(
            resource=replication_resource_element.target,
            src_img_ref=str(replication_resource_element.src_ref),
        )

        return replication_resource_element


class ExtraTagUploader(UploaderBase):
    '''
    Uploader that will push additional (static) tags to uploaded images. Useful to e.g. add
    `latest` tag. Extra-Tags will be overwritten as a hardcoded behaviour of this uploader.
    '''
    def __init__(
        self,
        extra_tags: collections.abc.Iterable[str],
    ):
        self.extra_tags = list(extra_tags)

    def process(
        self,
        replication_resource_element: ctt.model.ReplicationResourceElement,
        /,
        **kwargs,
    ) -> ctt.model.ReplicationResourceElement:
        replication_resource_element.extra_tags = self.extra_tags

        return replication_resource_element


class DigestUploader(UploaderBase):
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

    def process(
        self,
        replication_resource_element: ctt.model.ReplicationResourceElement,
        /,
        **kwargs,
    ) -> ctt.model.ReplicationResourceElement:
        replication_resource_element.reference_by_digest = True
        replication_resource_element.retain_symbolic_tag = self._retain_symbolic_tag

        return replication_resource_element
