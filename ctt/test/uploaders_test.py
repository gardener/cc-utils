# SPDX-FileCopyrightText: 2021 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
import gci.componentmodel as cm

import ctt.uploaders as uploaders


def test_labels_with_migration_hint_doesnt_overwrite_original_ref():
    firstLabel = cm.Label(
        name='another_label',
        value='test'
    )
    secondLabel = cm.Label(
        name=uploaders.original_ref_label_name,
        value='original-repo-0.com/my-image:1.0.0'
    )

    res = cm.Resource(
        name='my-image',
        version='1.0.0',
        type=cm.ResourceType.OCI_IMAGE,
        access=cm.OciAccess(
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
    firstLabel = cm.Label(
        name='another_label',
        value='test'
    )
    expectedOriginalRefLabel = cm.Label(
        name=uploaders.original_ref_label_name,
        value='original-repo.com/my-image:1.0.0'
    )

    res = cm.Resource(
        name='my-image',
        version='1.0.0',
        type=cm.ResourceType.OCI_IMAGE,
        access=cm.OciAccess(
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
