import base64
import collections.abc
import dataclasses
import enum
import json
import os

import dacite
import requests
import yaml

import oci.auth
import oci.aws
import oci.model

own_dir = os.path.dirname(__file__)
cfgs_path = os.path.join(own_dir, 'oidc-cfgs.yaml')


@dataclasses.dataclass
class OidcConfiguration:
    name: str
    type: oci.model.OciRegistryType
    oci_repository_prefixes: list[str]
    github_host: str
    github_orgs: list[str] | None

    @staticmethod
    def from_dict(raw: dict):
        registry_type = oci.model.OciRegistryType(raw['type'])

        data_class = {
            oci.model.OciRegistryType.AWS: AwsOidcConfiguration,
            oci.model.OciRegistryType.AZURE: AzureOidcConfiguration,
            oci.model.OciRegistryType.GAR: GarOidcConfiguration,
        }.get(registry_type)

        if not data_class:
            print(f'Error: Unsupported {registry_type=}')
            exit(1)

        return dacite.from_dict(
            data_class=data_class,
            data=raw,
            config=dacite.Config(
                cast=[enum.Enum],
            ),
        )


@dataclasses.dataclass
class AwsOidcConfiguration(OidcConfiguration):
    role_to_assume: str
    audience: str = 'sts.amazonaws.com'
    session_name: str = 'GitHubActions'


@dataclasses.dataclass
class AzureOidcConfiguration(OidcConfiguration):
    client_id: str
    tenant_id: str
    audience: str = 'api://AzureADTokenExchange'


@dataclasses.dataclass
class GarOidcConfiguration(OidcConfiguration):
    project_name: str
    project_id: int
    service_account: str
    identity_pool_name: str
    identity_provider_name: str

    @property
    def workload_identity_provider(self) -> str:
        return (
            f'projects/{self.project_id}/locations/global/workloadIdentityPools/'
            f'{self.identity_pool_name}/providers/{self.identity_provider_name}'
        )

    @property
    def audience(self) -> str:
        return f'//iam.googleapis.com/{self.workload_identity_provider}'


def find_oidc_cfg(
    image_reference: oci.model.OciImageReference,
) -> AwsOidcConfiguration | AzureOidcConfiguration | GarOidcConfiguration:
    github_server_url = os.environ['GITHUB_SERVER_URL']
    github_org = os.environ['GITHUB_REPOSITORY_OWNER']

    if github_server_url.startswith('https://github.tools'):
        github_host = 'gh-tools'
    elif github_server_url.startswith('https://github.wdf'):
        github_host = 'gh-wdf'
    elif github_server_url == 'https://github.com':
        github_host = 'gh-com'
    else:
        print(f'Error: Unsupported {github_server_url=}')
        exit(1)

    with open(cfgs_path) as file:
        oidc_cfgs_raw = yaml.safe_load(file)

    oidc_cfgs = [
        OidcConfiguration.from_dict(oidc_cfg_raw)
        for oidc_cfg_raw in oidc_cfgs_raw
    ]

    prefixes = set()
    for oidc_cfg in oidc_cfgs:
        if oidc_cfg.type is not image_reference.registry_type:
            continue

        if oidc_cfg.github_host != github_host:
            continue

        prefixes.update(oidc_cfg.oci_repository_prefixes)

        if oidc_cfg.github_orgs and github_org not in oidc_cfg.github_orgs:
            continue

        for prefix in oidc_cfg.oci_repository_prefixes:
            if str(image_reference).startswith(prefix):
                return oidc_cfg

    print(f'Did not find matching OIDC cfg for {image_reference=}')
    print(f'Known prefixes for {github_host=} and {image_reference.registry_type=}:')
    for prefix in prefixes:
        print(f' - {prefix}')
    exit(1)


def _fetch_with_retries(
    url: str,
    session: requests.Session,
    method: str='GET',
    json: dict | None=None,
    data: dict | None=None,
    headers: dict | None=None,
    remaining_retries: int=3,
) -> requests.Response:
    res = session.request(
        method=method,
        url=url,
        json=json,
        data=data,
        headers=headers,
    )

    if not res.ok:
        print(f'WARNING: rq against {url=} failed: {res.status_code=} {res.reason=} {res.content=}')

        if remaining_retries > 0:
            print(f'Retrying... ({remaining_retries=})')
            return _fetch_with_retries(
                url=url,
                session=session,
                method=method,
                json=json,
                data=data,
                headers=headers,
                remaining_retries=remaining_retries - 1,
            )

    res.raise_for_status()

    return res


def authenticate_against_aws(
    oidc_cfg: AwsOidcConfiguration,
    gh_token: str,
    gh_token_url: str,
    lifetime_seconds: int=3600,
) -> dict[str, str]:
    '''
    See https://github.com/aws-actions/configure-aws-credentials for reference.
    '''
    session = requests.Session()

    res = _fetch_with_retries(
        url=f'{gh_token_url}&audience={oidc_cfg.audience}',
        session=session,
        headers={
            'Authorization': f'Bearer {gh_token}',
        },
    )
    gh_oidc_token = res.json()['value']

    res = _fetch_with_retries(
        url=(
            f'https://sts.amazonaws.com/'
            '?Action=AssumeRoleWithWebIdentity'
            f'&DurationSeconds={lifetime_seconds}'
            f'&RoleArn={oidc_cfg.role_to_assume}'
            f'&RoleSessionName={oidc_cfg.session_name}'
            f'&WebIdentityToken={gh_oidc_token}'
            '&Version=2011-06-15'
        ),
        session=session,
        method='POST',
        headers={
            'Accept': 'application/json',
        },
    )
    creds = res.json()['AssumeRoleWithWebIdentityResponse']['AssumeRoleWithWebIdentityResult']['Credentials'] # noqa: E501
    access_key_id = creds['AccessKeyId']
    secret_access_key = creds['SecretAccessKey']
    session_token = creds['SessionToken']

    return {
        'access_key_id': access_key_id,
        'secret_access_key': secret_access_key,
        'session_token': session_token,
    }


def authenticate_against_azure(
    oidc_cfg: AzureOidcConfiguration,
    gh_token: str,
    gh_token_url: str,
    registry: str,
) -> dict[str, str]:
    '''
    See https://github.com/Azure/login for reference.
    '''
    session = requests.Session()

    res = _fetch_with_retries(
        url=f'{gh_token_url}&audience={oidc_cfg.audience}',
        session=session,
        headers={
            'Authorization': f'Bearer {gh_token}',
        },
    )
    gh_oidc_token = res.json()['value']

    body = {
        'client_id': oidc_cfg.client_id,
        'scope': 'https://management.azure.com/.default',
        'grant_type': 'client_credentials',
        'client_assertion_type': 'urn:ietf:params:oauth:client-assertion-type:jwt-bearer',
        'client_assertion': gh_oidc_token,
    }
    res = _fetch_with_retries(
        url=f'https://login.microsoftonline.com/{oidc_cfg.tenant_id}/oauth2/v2.0/token',
        session=session,
        method='POST',
        data=body,
        headers={
            'Content-Type': 'application/x-www-form-urlencoded',
        },
    )
    auth_token = res.json()['access_token']

    body = {
        'grant_type': 'access_token',
        'service': registry,
        'access_token': auth_token,
    }
    res = _fetch_with_retries(
        url=f'https://{registry}/oauth2/exchange',
        session=session,
        method='POST',
        data=body,
        headers={
            'Content-Type': 'application/x-www-form-urlencoded',
        },
    )
    refresh_token = res.json()['refresh_token']

    username = '00000000-0000-0000-0000-000000000000'

    token = base64.b64encode(f'{username}:{refresh_token}'.encode()).decode()

    return {
        'auth': token,
    }


def authenticate_against_gar(
    oidc_cfg: GarOidcConfiguration,
    gh_token: str,
    gh_token_url: str,
    lifetime_seconds: int=3600,
) -> dict[str, str]:
    '''
    See https://github.com/google-github-actions/auth for reference.
    '''
    session = requests.Session()

    res = _fetch_with_retries(
        url=f'{gh_token_url}&audience={oidc_cfg.audience}',
        session=session,
        headers={
            'Authorization': f'Bearer {gh_token}',
        },
    )
    gh_oidc_token = res.json()['value']

    body = {
        'audience': oidc_cfg.audience,
        'grantType': 'urn:ietf:params:oauth:grant-type:token-exchange',
        'requestedTokenType': 'urn:ietf:params:oauth:token-type:access_token',
        'scope': 'https://www.googleapis.com/auth/cloud-platform',
        'subjectTokenType': 'urn:ietf:params:oauth:token-type:jwt',
        'subjectToken': gh_oidc_token,
    }
    res = _fetch_with_retries(
        url='https://sts.googleapis.com/v1/token',
        session=session,
        method='POST',
        json=body,
    )
    auth_token = res.json()['access_token']

    gar_access_token_url = (
        'https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/'
        f'{oidc_cfg.service_account}:generateAccessToken'
    )
    body = {
        'scope': 'https://www.googleapis.com/auth/cloud-platform',
        'lifetime': f'{lifetime_seconds}s',
    }
    res = _fetch_with_retries(
        url=gar_access_token_url,
        session=session,
        method='POST',
        json=body,
        headers={
            'Authorization': f'Bearer {auth_token}',
        },
    )
    access_token = res.json()['accessToken']

    username = 'oauth2accesstoken'

    token = base64.b64encode(f'{username}:{access_token}'.encode()).decode()

    return {
        'auth': token,
    }


def authenticate_against_ghcr() -> dict[str, str] | None:
    username = os.environ['GITHUB_ACTOR']
    password = os.environ['GITHUB_TOKEN']

    if not username or not password:
        return None

    token = base64.b64encode(f'{username}:{password}'.encode()).decode()

    return {
        'auth': token,
    }


def write_docker_config(
    image_references: collections.abc.Iterable[str],
    docker_cfg_path: str,
    extra_auths: dict | None=None,
) -> dict:
    try:
        gh_token = os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']
        gh_token_url = os.environ['ACTIONS_ID_TOKEN_REQUEST_URL']
    except KeyError:
        print('Error: the following environment-variables are not set:')
        print('- ACTIONS_ID_TOKEN_REQUEST_TOKEN')
        print('- ACTIONS_ID_TOKEN_REQUEST_URL')
        print()
        print('This typically indicates the job was not run with needed permission:')
        print('  id-token: write')
        exit(1)

    auths = {}

    for image_reference in image_references:
        image_reference = oci.model.OciImageReference(image_reference)

        if image_reference.netloc in auths:
            # first defined image-reference should win
            continue

        registry_type = image_reference.registry_type

        if registry_type is oci.model.OciRegistryType.UNKNOWN:
            print(f'Warning: {image_reference=} has unknown registry-type; will not retrieve')
            print( '         access-token via OIDC') # noqa: E201
            continue

        if registry_type is not oci.model.OciRegistryType.GHCR:
            oidc_cfg = find_oidc_cfg(
                image_reference=image_reference,
            )
            print(f'info: will use {oidc_cfg=} for {image_reference=}')

        if registry_type is oci.model.OciRegistryType.AWS:
            auth = authenticate_against_aws(
                oidc_cfg=oidc_cfg,
                gh_token=gh_token,
                gh_token_url=gh_token_url,
            )

        elif registry_type is oci.model.OciRegistryType.AZURE:
            auth = authenticate_against_azure(
                oidc_cfg=oidc_cfg,
                gh_token=gh_token,
                gh_token_url=gh_token_url,
                registry=image_reference.netloc,
            )

        elif registry_type is oci.model.OciRegistryType.GAR:
            auth = authenticate_against_gar(
                oidc_cfg=oidc_cfg,
                gh_token=gh_token,
                gh_token_url=gh_token_url,
            )

        elif registry_type is oci.model.OciRegistryType.GHCR:
            auth = authenticate_against_ghcr()

        else:
            print(f'Error: Unsupported {registry_type=}')
            exit(1)

        if not auth:
            continue

        netloc = image_reference.netloc
        print(f'info: adding auth for {image_reference=} ({netloc=})')
        if netloc in auths:
            print(f'warning: overwriting auth for {netloc=}')
        auths[netloc] = auth

    if extra_auths:
        auths |= extra_auths

    docker_cfg = {
      'auths': auths,
    }

    with open(docker_cfg_path, 'w') as file:
        json.dump(docker_cfg, file)

    return docker_cfg
