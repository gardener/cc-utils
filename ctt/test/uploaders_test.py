# SPDX-FileCopyrightText: 2021 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
import ocm

import ctt.uploaders as uploaders


def test_labels_with_migration_hint_doesnt_overwrite_original_ref():
    firstLabel = ocm.Label(
        name='another_label',
        value='test'
    )
    secondLabel = ocm.Label(
        name=uploaders.original_ref_label_name,
        value='original-repo-0.com/my-image:1.0.0'
    )

    res = ocm.Resource(
        name='my-image',
        version='1.0.0',
        type=ocm.ArtefactType.OCI_IMAGE,
        access=ocm.OciAccess(
            imageReference='target-repo.com/my-image:1.0.0',
        ),
        labels=[
            firstLabel,
            secondLabel,
        ]
    )

    labels = uploaders.labels_with_migration_hint(res, 'original-repo-1.com/my-image:1.0.0')

    assert len(labels) == 2
    assert labels[0] == firstLabel
    assert labels[1] == secondLabel


def test_labels_with_migration_hint_adds_original_ref_if_not_present():
    firstLabel = ocm.Label(
        name='another_label',
        value='test'
    )
    expectedOriginalRefLabel = ocm.Label(
        name=uploaders.original_ref_label_name,
        value='original-repo.com/my-image:1.0.0'
    )

    res = ocm.Resource(
        name='my-image',
        version='1.0.0',
        type=ocm.ArtefactType.OCI_IMAGE,
        access=ocm.OciAccess(
            imageReference='target-repo.com/my-image:1.0.0',
        ),
        labels=[
            firstLabel,
        ]
    )

    labels = uploaders.labels_with_migration_hint(res, 'original-repo.com/my-image:1.0.0')

    assert len(labels) == 2
    assert labels[0] == firstLabel
    assert labels[1] == expectedOriginalRefLabel
