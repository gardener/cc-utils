# SPDX-FileCopyrightText: 2021 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import pytest

import ctt.model
import ctt.processors as processors


@pytest.fixture
def replication_resource_element():
    return ctt.model.ReplicationResourceElement(
        source=None,
        target=None,
        component_id=None,
    )


def test_noop_processor(replication_resource_element):
    examinee = processors.NoOpProcessor()

    result = examinee.process(replication_resource_element)

    assert result is replication_resource_element


def test_filefilter_processor(replication_resource_element, tmpdir):
    filter_file = tmpdir.join('filters')
    filter_file.write('remove/me')

    examinee = processors.FileFilter(
        filter_files=[filter_file],
    )

    result = examinee.process(replication_resource_element)
    remove_files = result.remove_files

    assert remove_files == ['remove/me',]
