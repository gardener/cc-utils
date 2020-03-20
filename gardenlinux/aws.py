import boto3
import boto3.session

import botocore.exceptions


def absent_ok(client_error: botocore.exceptions.ClientError):
    http_code = int(client_error.response['Error']['Code'])
    if not http_code == 404:
        raise client_error


def bucket_exists(s3_client, bucket_name):
    try:
        s3_client.head_bucket(Bucket=bucket_name)
        return True
    except botocore.exceptions.ClientError as ce:
        absent_ok(ce)
        return False


def create_bucket(session: boto3.session.Session, bucket_name):
    s3_client = session.client('s3')

    if not bucket_exists(s3client=s3client, bucket_name=bucket_name):
        s3_client.create_bucket(
            ACL='public-read',
            Bucket=bucket_name,
            CreateBucketConfiguration={
                'LocationConstraint': s3_client.meta.region_name,
            },
        )


def upload_fileobj(s3_client, bucket_name, fileobj, dest_name):
    s3_client.upload_fileobj(
            fileobj,
            bucket_name,
            dest_name,
    )
