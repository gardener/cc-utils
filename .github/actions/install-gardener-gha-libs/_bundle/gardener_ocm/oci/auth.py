import base64
import collections.abc
import dataclasses
import enum
import json
import logging
import operator
import os
import shutil
import subprocess
import threading
import time

import requests

import oci.util

logger = logging.getLogger(__name__)


class AuthType(enum.StrEnum):
    BASIC_AUTH = 'basic_auth'


class CredentialHelperPolicy(enum.StrEnum):
    DISABLED     = 'disabled'      # skip helpers entirely — static auths only
    STATIC_FIRST = 'static_first'  # static auths → helpers (safe default)
    WARN         = 'warn'          # helpers → static; missing/broken helper → log warning
    FAIL         = 'fail'          # helpers → static; missing/broken helper → raise


class Privileges(enum.Enum):
    READONLY = 'readonly'
    READWRITE = 'readwrite'
    ADMIN = 'admin'

    def _asint(self, privileges):
        if privileges is self.READONLY:
            return 0
        elif privileges is self.READWRITE:
            return 1
        elif privileges is self.ADMIN:
            return 2
        elif privileges is None:
            return 4
        else:
            raise NotImplementedError(privileges)

    def __hash__(self):
        return self._asint(self).__hash__()

    def __lt__(self, other):
        o = self._asint(other)
        return self._asint(self).__lt__(o)

    def __le__(self, other):
        o = self._asint(other)
        return self._asint(self).__le__(o)

    def __eq__(self, other):
        o = self._asint(other)
        return self._asint(self).__eq__(o)

    def __ne__(self, other):
        o = self._asint(other)
        return self._asint(self).__ne__(o)

    def __gt__(self, other):
        o = self._asint(other)
        return self._asint(self).__gt__(o)

    def __ge__(self, other):
        o = self._asint(other)
        return self._asint(self).__ge__(o)


@dataclasses.dataclass(frozen=True)
class OciCredentials:
    pass


@dataclasses.dataclass(frozen=True)
class OciConfig:
    privileges: Privileges
    credentials: OciCredentials
    url_prefixes: collections.abc.Sequence[str] = dataclasses.field(default_factory=tuple)

    def valid_for(self, image_reference: str, privileges: Privileges=Privileges.READONLY):
        if privileges and privileges > self.privileges:
            return False

        if not self.url_prefixes:
            return True

        unmodified_ref = image_reference.lower()
        image_reference = oci.util.normalise_image_reference(image_reference=image_reference).lower()

        for prefix in self.url_prefixes:
            prefix = prefix.lower()

            if image_reference.startswith(oci.util.normalise_image_reference(prefix)):
                return True
            if image_reference.startswith(prefix.lower()):
                return True
            if unmodified_ref.startswith(prefix):
                return True

        return False


@dataclasses.dataclass(frozen=True)
class OciBasicAuthCredentials(OciCredentials):
    username: str
    password: str


@dataclasses.dataclass(frozen=True)
class OciAccessKeyCredentials(OciCredentials):
    access_key_id: str
    secret_access_key: str
    session_token: str | None = None


# typehint-alias
image_reference = str
credentials_lookup = collections.abc.Callable[[image_reference, Privileges, bool], OciCredentials]


def mk_credentials_lookup(
    cfgs: OciCredentials | collections.abc.Sequence[OciCredentials],
) -> collections.abc.Callable[[image_reference, Privileges, bool], OciConfig]:
    '''
    returns a callable that can be queried for matching OciCredentials for requested
    privileges and image-references
    '''
    if isinstance(cfgs, OciConfig):
        cfgs = (cfgs,)

    def lookup_credentials(
        image_reference: str,
        privileges: Privileges=Privileges.READONLY,
        absent_ok: bool=False,
    ):
        valid_cfgs = sorted(
          (
            c for c in cfgs
            if c.valid_for(image_reference=image_reference, privileges=privileges)
          ),
          key=operator.attrgetter('privileges'),
        )

        if not valid_cfgs and absent_ok:
            return None

        if not valid_cfgs:
            raise ValueError(f'no valid cfg found: {image_reference=}, {privileges=}')

        # first element contains cfg with least required privileges
        return valid_cfgs[0].credentials

    return lookup_credentials


def _invoke_credential_helper(
    helper_name: str,
    server_url: str,
    policy: CredentialHelperPolicy,
    timeout_seconds: int | None=60,
) -> OciBasicAuthCredentials | None:
    '''
    Invokes a docker credential helper binary (`docker-credential-<helper_name> get`), passing
    server_url on stdin. Returns OciBasicAuthCredentials on success, None if the helper reports
    no credentials (exit 1 with "credentials not found" message).

    Behaviour when the binary is not found on PATH, invocation fails, or timeout is exceeded is
    governed by policy: WARN → log warning and return None; FAIL → raise RuntimeError.

    timeout_seconds controls subprocess timeout (default: 60). Pass None to disable — useful
    when helpers may trigger interactive flows (e.g. browser-based OAuth).
    '''
    binary = f'docker-credential-{helper_name}'

    def _handle_unavailable(msg: str) -> None:
        if policy is CredentialHelperPolicy.FAIL:
            raise RuntimeError(msg)
        logger.warning(msg)

    if not shutil.which(binary):
        _handle_unavailable(
            f'credential helper {binary!r} configured in docker-cfg but not found on PATH'
        )
        return None

    try:
        result = subprocess.run(
            [binary, 'get'],
            input=server_url,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        _handle_unavailable(f'credential helper {binary!r} timed out after {timeout_seconds}s')
        return None
    except OSError as e:
        _handle_unavailable(f'failed to invoke credential helper {binary!r}: {e}')
        return None

    if result.returncode != 0:
        # helper signals "no credentials stored" via non-zero exit; not an error worth warning about
        logger.debug(
            f'credential helper {binary!r} returned {result.returncode}'
            f' for {server_url!r}: {result.stderr.strip()}'
        )
        return None

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        _handle_unavailable(f'credential helper {binary!r} returned invalid JSON: {e}')
        return None

    username = payload.get('Username', '')
    secret = payload.get('Secret', '')

    if not username or not secret:
        _handle_unavailable(f'credential helper {binary!r} returned incomplete credentials')
        return None

    return OciBasicAuthCredentials(username=username, password=secret)


def docker_credentials_lookup(
    docker_cfg: str | None=None,
    absent_ok: bool=False,
    credential_helper_policy: CredentialHelperPolicy=CredentialHelperPolicy.STATIC_FIRST,
    credential_helper_timeout_seconds: int | None=60,
) -> collections.abc.Callable[[image_reference, Privileges, bool], OciConfig]:
    '''
    returns a credentials-lookup backed by docker's auth-config. By design, docker's auth-config
    only allows configuring credentials per hostname. By default docker-cfg is expected at
    `$HOME/.docker/config.json`. Location of docker-cfg can be customised via docker_cfg parameter.

    Supports credential helpers via `credHelpers` (per-registry) and `credsStore` (global fallback)
    fields in docker-cfg. Resolution order depends on credential_helper_policy (see below).

    Credential helper behaviour is governed by credential_helper_policy:
      DISABLED     → skip helpers entirely, use only static auths
      STATIC_FIRST → static auths first; helpers used only as fallback (default)
      WARN         → helpers first; missing/broken binary emits a warning and falls through
      FAIL         → helpers first; missing/broken binary raises RuntimeError

    credential_helper_timeout_seconds sets the subprocess timeout in seconds (default: 60). Pass
    None to disable — useful when helpers may trigger interactive flows (e.g. browser-based OAuth).

    if no docker-cfg is found, raises RuntimeError, unless absent_ok is truthy, in which case the
    returned lookup will never return any credentials (which might still be useful for readonly
    operations that for many registries allow anonymous access).

    Note that docker does not offer to configure credentials by permissions (hence privileges
    parameter will be ignored)
    '''
    if not docker_cfg:
        docker_cfg = os.path.join(os.environ.get('HOME', ''), '.docker/config.json')

    if not os.path.isfile(docker_cfg):
        if not absent_ok:
            raise RuntimeError(f'not an existing file: {docker_cfg=}')

        def find_nothing_lookup(
            image_reference: str,
            privileges: Privileges=Privileges.READONLY,
            absent_ok: bool=False,
        ):
            if not absent_ok:
                raise ValueError(f'no auth-cfg found in {docker_cfg=} for {image_reference=}')
            return None

        return find_nothing_lookup

    def docker_auth_lookup(
        image_reference: str,
        privileges: Privileges=Privileges.READONLY,
        absent_ok: bool=False,
    ):
        # re-read docker-cfg to reflect fs-updates
        with open(docker_cfg) as f:
            docker_auth = json.load(f)

        if image_reference.startswith('/'):
            # relative reference - no means to find appropriate cfg
            if not absent_ok:
                raise ValueError(f'no auth-cfg found in {docker_cfg=} for {image_reference=}')
            return None

        # ignore ports - match cfg only by hostname
        image_netloc = image_reference.split('/')[0]
        image_host = image_netloc.split(':')[0]

        cred_helpers = docker_auth.get('credHelpers', {})
        creds_store = docker_auth.get('credsStore')
        helpers_enabled = credential_helper_policy is not CredentialHelperPolicy.DISABLED

        # prefer exact netloc match (host+port), fall back to host-only — consistent with auths
        # lookup; pass configured key as server_url so helpers receive exactly what the user
        # configured (e.g. 'localhost:5000')
        matched_helper_key = next(
            (k for k in cred_helpers if k == image_netloc),
            None,
        ) or next(
            (k for k in cred_helpers if k.split(':')[0] == image_host),
            None,
        )

        def lookup_static():
            auths = docker_auth.get('auths')
            if not auths:
                return None
            for netloc, auth_dict in auths.items():
                if netloc.split(':')[0] == image_host:
                    break
            else:
                return None

            if (
                (access_key_id := auth_dict.get('access_key_id'))
                and (secret_access_key := auth_dict.get('secret_access_key'))
            ):
                return OciAccessKeyCredentials(
                    access_key_id=access_key_id,
                    secret_access_key=secret_access_key,
                    session_token=auth_dict.get('session_token'),
                )

            auth = auth_dict.get('auth')
            if not auth:
                if not absent_ok:
                    raise ValueError(
                        f'did not find expected attr `auth` in {docker_cfg=} for {image_host=}'
                    )
                return None

            auth = base64.b64decode(auth).decode('utf-8')
            username, passwd = auth.split(':', 1)
            return OciBasicAuthCredentials(username=username, password=passwd)

        def lookup_helpers():
            if not helpers_enabled:
                return None
            if matched_helper_key is not None:
                return _invoke_credential_helper(
                    helper_name=cred_helpers[matched_helper_key],
                    server_url=matched_helper_key,
                    policy=credential_helper_policy,
                    timeout_seconds=credential_helper_timeout_seconds,
                )
            if creds_store:
                return _invoke_credential_helper(
                    helper_name=creds_store,
                    server_url=image_host,
                    policy=credential_helper_policy,
                    timeout_seconds=credential_helper_timeout_seconds,
                )
            return None

        # STATIC_FIRST and DISABLED: static takes precedence
        # WARN and FAIL: helpers take precedence (Docker-native order)
        if credential_helper_policy in (
            CredentialHelperPolicy.STATIC_FIRST,
            CredentialHelperPolicy.DISABLED,
        ):
            creds = lookup_static() or lookup_helpers()
        else:
            creds = lookup_helpers() or lookup_static()

        if creds is not None:
            return creds

        if not absent_ok:
            raise ValueError(
                f'no matching auth-cfg found in {docker_cfg=} for {image_reference=}'
            )
        return None

    return docker_auth_lookup


def exchange_gar_token(
    oidc_cfg,
    gh_token: str,
    gh_token_url: str,
    lifetime_seconds: int=3600,
) -> str:
    '''
    Exchange a GitHub OIDC token for a GAR/GCR access token via GCP STS and IAM.

    oidc_cfg must have .audience and .service_account attributes
    (compatible with GarOidcConfiguration from .github/actions/oci-auth/oidc.py).

    Returns the raw GAR access token string.
    '''
    session = requests.Session()

    def _fetch(url, method='GET', json_body=None, headers=None, remaining=3):
        res = session.request(
            method=method,
            url=url,
            json=json_body,
            headers=headers,
        )
        if not res.ok and remaining > 0:
            return _fetch(url, method, json_body, headers, remaining - 1)
        res.raise_for_status()
        return res

    res = _fetch(
        url=f'{gh_token_url}&audience={oidc_cfg.audience}',
        headers={'Authorization': f'Bearer {gh_token}'},
    )
    gh_oidc_token = res.json()['value']

    res = _fetch(
        url='https://sts.googleapis.com/v1/token',
        method='POST',
        json_body={
            'audience': oidc_cfg.audience,
            'grantType': 'urn:ietf:params:oauth:grant-type:token-exchange',
            'requestedTokenType': 'urn:ietf:params:oauth:token-type:access_token',
            'scope': 'https://www.googleapis.com/auth/cloud-platform',
            'subjectTokenType': 'urn:ietf:params:oauth:token-type:jwt',
            'subjectToken': gh_oidc_token,
        },
    )
    sts_token = res.json()['access_token']

    res = _fetch(
        url=(
            'https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/'
            f'{oidc_cfg.service_account}:generateAccessToken'
        ),
        method='POST',
        json_body={
            'scope': 'https://www.googleapis.com/auth/cloud-platform',
            'lifetime': f'{lifetime_seconds}s',
        },
        headers={'Authorization': f'Bearer {sts_token}'},
    )
    return res.json()['accessToken']


class _RefreshableGarCredentialsLookup:
    def __init__(self, oidc_cfg, prefetch_margin_seconds: int=300):
        self._oidc_cfg = oidc_cfg
        self._prefetch_margin_seconds = prefetch_margin_seconds
        self._token: str | None = None
        self._expires_at: float = 0.0  # time.monotonic() timestamp
        self._lock = threading.Lock()

    def _needs_refresh(self) -> bool:
        return (
            self._token is None
            or time.monotonic() >= self._expires_at - self._prefetch_margin_seconds
        )

    def _refresh(self):
        # caller must hold _lock
        gh_token = os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']
        gh_token_url = os.environ['ACTIONS_ID_TOKEN_REQUEST_URL']
        self._token = exchange_gar_token(
            oidc_cfg=self._oidc_cfg,
            gh_token=gh_token,
            gh_token_url=gh_token_url,
        )
        # exchange_gar_token requests a 3600 s token by default
        self._expires_at = time.monotonic() + 3600

    def invalidate_cache(self):
        with self._lock:
            self._token = None
            self._expires_at = 0.0

    def __call__(
        self,
        image_reference: str,
        privileges: 'Privileges'=None,
        absent_ok: bool=False,
    ) -> OciBasicAuthCredentials | None:
        import oci.model as om
        ref = om.OciImageReference(image_reference)
        if ref.registry_type not in (om.OciRegistryType.GAR, om.OciRegistryType.GCR):
            return None

        with self._lock:
            if self._needs_refresh():
                self._refresh()
            token = self._token

        return OciBasicAuthCredentials(username='oauth2accesstoken', password=token)


def make_refreshable_gar_credentials_lookup(
    oidc_cfg,
    prefetch_margin_seconds: int=300,
) -> _RefreshableGarCredentialsLookup:
    return _RefreshableGarCredentialsLookup(
        oidc_cfg=oidc_cfg,
        prefetch_margin_seconds=prefetch_margin_seconds,
    )
