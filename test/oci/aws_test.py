import json
import unittest.mock

import oci.auth
import oci.aws as aws


ECR_REF = '123456789012.dkr.ecr.eu-west-1.amazonaws.com/my-repo:latest'


def _credentials():
    return oci.auth.OciAccessKeyCredentials(
        access_key_id='AKIATEST',
        secret_access_key='secret',
        session_token=None,
    )


def _mock_response(failures=None):
    r = unittest.mock.Mock()
    r.ok = True
    r.json.return_value = {'imageIds': [], 'failures': failures or []}
    r.raise_for_status.return_value = None
    return r


def test_batch_delete_images_single_chunk():
    image_ids = [{'imageTag': f'v{i}'} for i in range(5)]

    with unittest.mock.patch('oci.aws.request', return_value=_mock_response()) as mock_req:
        failures = aws.batch_delete_images(
            image_reference=ECR_REF,
            image_ids=image_ids,
            credentials=_credentials(),
        )

    assert failures == []
    assert mock_req.call_count == 1
    body = json.loads(mock_req.call_args.kwargs['body'])
    assert body['repositoryName'] == 'my-repo'
    assert body['registryId'] == '123456789012'
    assert body['imageIds'] == image_ids


def test_batch_delete_images_multiple_chunks():
    image_ids = [{'imageTag': f'v{i}'} for i in range(150)]

    with unittest.mock.patch('oci.aws.request', return_value=_mock_response()) as mock_req:
        aws.batch_delete_images(
            image_reference=ECR_REF,
            image_ids=image_ids,
            credentials=_credentials(),
        )

    assert mock_req.call_count == 2
    first_body = json.loads(mock_req.call_args_list[0].kwargs['body'])
    second_body = json.loads(mock_req.call_args_list[1].kwargs['body'])
    assert len(first_body['imageIds']) == 100
    assert len(second_body['imageIds']) == 50


def test_batch_delete_images_returns_failures():
    failure = {
        'imageId': {'imageTag': 'v1'},
        'failureCode': 'ImageNotFoundException',
        'failureReason': 'not found',
    }

    with unittest.mock.patch('oci.aws.request', return_value=_mock_response(failures=[failure])):
        result = aws.batch_delete_images(
            image_reference=ECR_REF,
            image_ids=[{'imageTag': 'v1'}],
            credentials=_credentials(),
        )

    assert result == [failure]
