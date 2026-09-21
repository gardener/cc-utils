# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

import ctt.model
import ctt.uploaders as uploaders
import ocm


def _element(image_reference: str) -> ctt.model.ReplicationResourceElement:
    resource = ocm.Resource(
        name='my-image',
        version='v1',
        type=ocm.ArtefactType.OCI_IMAGE,
        access=ocm.OciAccess(imageReference=image_reference),
    )
    return ctt.model.ReplicationResourceElement(
        source=resource,
        target=resource,
        component_id=None,
        src_ocm_repo=None,
    )


def _fold(image_reference: str, **kwargs) -> str:
    examinee = uploaders.TagFoldingUploader(**kwargs)
    result = examinee.process(_element(image_reference), target_as_source=True)
    return result.target.access.imageReference


def test_folds_symbolic_tag_into_repository():
    assert _fold('tgt.example.com/my-image:v1') == 'tgt.example.com/my-image-v1:v1'
    assert _fold('tgt.example.com/my-image:v2') == 'tgt.example.com/my-image-v2:v2'


def test_mapping_is_stable_and_order_independent():
    # a pure function -> same input always yields same output
    assert _fold('tgt.example.com/my-image:v3') == _fold('tgt.example.com/my-image:v3')


def test_distinct_images_never_collide():
    # different base -> different repository, regardless of shared tag
    assert _fold('tgt.example.com/a:v1') != _fold('tgt.example.com/b:v1')


def test_mangles_illegal_characters_and_lowercases():
    assert _fold('tgt.example.com/img:1.0.0+build') == 'tgt.example.com/img-1.0.0_build:1.0.0+build'
    assert _fold('tgt.example.com/img:V1') == 'tgt.example.com/img-v1:V1'


def test_custom_separator_and_replacement_char():
    ref = _fold(
        'tgt.example.com/img:1+2',
        separator='.',
        mangle_replacement_char='-',
    )
    assert ref == 'tgt.example.com/img.1-2:1+2'


def test_digest_tag_folds_and_marks_reference_by_digest():
    digest = 'sha256:' + 'a' * 64
    examinee = uploaders.TagFoldingUploader()

    result = examinee.process(
        _element(f'tgt.example.com/my-image@{digest}'),
        target_as_source=True,
    )

    assert result.target.access.imageReference == f'tgt.example.com/my-image-sha256_{"a" * 64}@{digest}' # noqa: E501
    assert result.reference_by_digest is True


def test_mixed_tag_folds_on_symbolic_and_retains_both():
    digest = 'sha256:' + 'b' * 64
    result = uploaders.TagFoldingUploader().process(
        _element(f'tgt.example.com/my-image:v1@{digest}'),
        target_as_source=True,
    )

    assert result.target.access.imageReference == f'tgt.example.com/my-image-v1:v1@{digest}'
    assert result.reference_by_digest is True
