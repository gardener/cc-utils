import pytest

import container.model

import processing.processors as processors
import processing.processing_model as processing_model


@pytest.fixture
def job():
    return processing_model.ProcessingJob(
        component=None,
        container_image=None,
        download_request=None,
        upload_request=container.model.ContainerImageUploadRequest(
            source_file='file:path',
            source_ref='source:ref',
            target_ref='target:ref',
            processing_callback=None,
        ),
        upload_context_url=None,
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
    callback = upload_request.processing_callback

    assert callable(callback)

    # XXX for a correct implementation, processor would not be required to return a partial function
    assert set(callback.keywords['remove_entries']) == set(('remove/me',))
