'''
This module contains special handling for interactions with the AWS Elastic Container Registry.
The main functionality comprises the AWS Signature Version 4 implementation required for the HTTP
api, as well as the authentication via access key and creation of repositories.
The boto3 package was not used here to avoid it as dependency in this (lightweight) OCI package.
'''
import base64
import datetime
import enum
import hashlib
import hmac
import json
import logging
import urllib.parse

import requests

import oci.auth
import oci.model


logger = logging.getLogger(__name__)


class AwsAction(enum.StrEnum):
    CREATE_REPOSITORY = 'CreateRepository'
    GET_AUTHORIZATION_TOKEN = 'GetAuthorizationToken'


def parse_aws_registry(
    image_reference: oci.model.OciImageReference | str,
) -> tuple[str, str, str]:
    '''
    This function makes the assumption that an AWS registry is always structured according to the
    following pattern: `<registry-id>.dkr.<service-name>.<region-name>.amazonaws.com`
    '''
    image_reference = oci.model.OciImageReference(image_reference)

    registry_id, dkr, service_name, region_name, aws = image_reference.netloc.split('.', maxsplit=4)

    if dkr != 'dkr' or aws != 'amazonaws.com':
        raise ValueError(
            'unexpected image reference netloc for aws, expected: '
            '"<registry-id>.dkr.<service-name>.<region-name>.amazonaws.com", actual: '
            f'"{image_reference.netloc}"'
        )

    return registry_id, service_name, region_name


def as_aws_api_url(
    image_reference: oci.model.OciImageReference | str,
) -> tuple[str, str, str]:
    image_reference = oci.model.OciImageReference(image_reference)

    _, service_name, region_name = parse_aws_registry(image_reference)

    return f'https://api.{service_name}.{region_name}.amazonaws.com/'


def prepare_headers(
    action: AwsAction,
    body: bytes,
    image_reference: oci.model.OciImageReference | str,
    method: str,
    credentials: oci.auth.OciAccessKeyCredentials,
    headers: dict | None=None,
) -> dict[str, str]:
    '''
    This function implements the AWS Signature Version 4 process according to
    https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_sigv.html. The results of this
    process are patched-into the `headers`.
    '''
    if not headers:
        headers = {}

    _, service_name, region_name = parse_aws_registry(image_reference)

    api_url = as_aws_api_url(image_reference)
    api_url_parsed = urllib.parse.urlparse(api_url)
    timestamp = datetime.datetime.now(tz=datetime.UTC).strftime('%Y%m%dT%H%M%SZ')

    headers |= {
        'Content-Type': 'application/x-amz-json-1.1',
        'X-Amz-Date': timestamp,
        'X-Amz-Target': f'AmazonEC2ContainerRegistry_V20150921.{action}',
    }

    signed_headers = 'content-type;host;x-amz-date;x-amz-target'

    canonical_request = '\n'.join([
        method.upper(),
        api_url_parsed.path,
        '',
        f'content-type:{headers["Content-Type"]}',
        f'host:{api_url_parsed.netloc}',
        f'x-amz-date:{headers["X-Amz-Date"]}',
        f'x-amz-target:{headers["X-Amz-Target"]}',
        '',
        signed_headers,
        hashlib.sha256(body).hexdigest(),
    ])

    scope = '/'.join([
        timestamp[0:8],
        region_name,
        service_name,
        'aws4_request',
    ])

    string_to_sign = '\n'.join([
        'AWS4-HMAC-SHA256',
        timestamp,
        scope,
        hashlib.sha256(canonical_request.encode()).hexdigest(),
    ])

    def sign(key: bytes, msg: str) -> hmac.HMAC:
        return hmac.new(key, msg.encode(), hashlib.sha256)

    key_date = sign(f'AWS4{credentials.secret_access_key}'.encode(), timestamp[0:8]).digest()
    key_region = sign(key_date, region_name).digest()
    key_service = sign(key_region, service_name).digest()
    key_signing = sign(key_service, 'aws4_request').digest()
    signature = sign(key_signing, string_to_sign).hexdigest()

    headers['Authorization'] = ', '.join([
        f'AWS4-HMAC-SHA256 Credential={credentials.access_key_id}/{scope}',
        f'SignedHeaders={signed_headers}',
        f'Signature={signature}',
    ])

    return headers


def request(
    action: AwsAction,
    body: bytes,
    image_reference: oci.model.OciImageReference | str,
    method: str,
    credentials: oci.auth.OciAccessKeyCredentials,
    session: requests.Session | None=None,
    headers: dict | None=None,
) -> requests.Response:
    if not session:
        session = requests.Session()

    url = as_aws_api_url(image_reference)

    headers = prepare_headers(
        action=action,
        body=body,
        image_reference=image_reference,
        method=method,
        credentials=credentials,
        headers=headers,
    )

    res = session.request(
        method=method,
        url=url,
        headers=headers,
        data=body,
    )

    if not res.ok:
        logger.warning(
            f'req against {url=} failed: {res.status_code=} {res.reason=} {res.content=} {headers=}'
        )

    res.raise_for_status()

    return res


def basic_auth_credentials(
    image_reference: oci.model.OciImageReference | str,
    credentials: oci.auth.OciAccessKeyCredentials,
    session: requests.Session | None=None,
) -> tuple[str, str]:
    '''
    AWS requires a short-lived password to be created from the access token together with the static
    username "AWS" for authentication.
    '''
    registry_id, _, _ = parse_aws_registry(image_reference=image_reference)

    body = json.dumps({
        'registryIds': [registry_id],
    }).encode()

    res = request(
        action=AwsAction.GET_AUTHORIZATION_TOKEN,
        body=body,
        image_reference=image_reference,
        method='POST',
        credentials=credentials,
        session=session,
    )

    # because we specified only a single registry-id, there must be only one token in the response
    token = res.json()['authorizationData'][0]['authorizationToken']

    token_decoded = base64.b64decode(token).decode()

    username, password = token_decoded.split(':')

    return username, password


def create_repository(
    image_reference: oci.model.OciImageReference | str,
    credentials: oci.auth.OciAccessKeyCredentials,
    session: requests.Session | None=None,
):
    image_reference = oci.model.OciImageReference(image_reference)

    registry_id, _, _ = parse_aws_registry(image_reference=image_reference)

    body = json.dumps({
        'registryId': registry_id,
        'repositoryName': image_reference.name,
    }).encode()

    logger.info(f'attempting to create AWS ECR repository {image_reference.name} for {registry_id=}')

    request(
        action=AwsAction.CREATE_REPOSITORY,
        body=body,
        image_reference=image_reference,
        method='POST',
        credentials=credentials,
        session=session,
    )
