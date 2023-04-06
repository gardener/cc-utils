# SPDX-FileCopyrightText: 2021 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import pytest

import ctt.processors as processors
import ctt.processing_model as processing_model


@pytest.fixture
def job():
    return processing_model.ProcessingJob(
        component=None,
        resource=None,
        upload_request=processing_model.ContainerImageUploadRequest(
            source_ref='source:ref',
            target_ref='target:ref',
            remove_files=(),
        ),
    )


def test_noop_processor(job):
    examinee = processors.NoOpProcessor()

    result = examinee.process(job)

    assert result is job


def test_filefilter_processor(job, tmpdir):
    filter_file = tmpdir.join('filters')
    filter_file.write('remove/me')

    examinee = processors.FileFilter(
        filter_files=[filter_file],
    )

    result = examinee.process(job)
    upload_request = result.upload_request
    remove_files = upload_request.remove_files

    assert remove_files == ('remove/me',)
