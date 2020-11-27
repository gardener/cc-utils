import dacite
import dataclasses
import datetime
import dateutil.parser
import requests
import requests.auth
import urllib.parse
import www_authenticate

import oci.auth as oa
import oci.model as om
import oci.util

urljoin = oci.util.urljoin


@dataclasses.dataclass(frozen=True)
class OauthToken:
    expires_in: int
    issued_at: str
    token: str
    scope: str

    def valid(self):
        issued_at = dateutil.parser.isoparse(self.issued_at)
        # pessimistically decuct 30s, to be on the safe side
        expiry_date = issued_at + datetime.timedelta(seconds=self.expires_in - 30)

        now = datetime.datetime.now(tz=datetime.timezone.utc)
        return now < expiry_date


class OauthTokenCache:
    def __init__(self):
        self.tokens = {}

    def token(self, scope: str):
        # purge expired tokens
        self.tokens = {s:t for s,t in self.tokens.items() if t.valid()}

        return self.tokens.get(scope)

    def set_token(self, token: OauthToken):
        if not token.valid():
            raise ValueError(f'token expired: {token=}')
        # TODO: we might compare remaining validity, and only replace existing tokens
        # if the new one has a later expiry date

        self.tokens[token.scope] = token


def parse_image_reference(image_reference: str):
    image_reference = oci.util.normalise_image_reference(image_reference)
    if not image_reference.startswith('https://'):
        image_reference = 'https://' + image_reference

    parsed_url = urllib.parse.urlparse(image_reference)
    return parsed_url


def _image_name(image_reference: str):
    image_name = parse_image_reference(image_reference=image_reference).path.lstrip('/')
    image_name = image_name.rsplit(':', 1)[0]

    return image_name


def base_api_url(image_reference: str) -> str:
    parsed_url = parse_image_reference(image_reference=image_reference)
    base_url = f'https://{parsed_url.netloc}'

    return urljoin(base_url, 'v2') + '/'


def artifact_base_url(image_reference: str) -> str:
    image_name = _image_name(image_reference=image_reference)

    return urljoin(
        base_api_url(image_reference=image_reference),
        image_name,
    )


def blob_url(image_reference: str, digest: str):
    return urljoin(
        artifact_base_url(image_reference),
        'blobs',
        digest
    )


def manifest_url(image_reference: str) -> str:
    last_part = image_reference.split('/')[-1]
    if ':' in last_part:
        tag = last_part.split(':')[-1]
    elif '@' in last_part:
        tag = last_part.split('@')[-1]
    else:
        raise ValueError(f'{image_reference=} does not seem to contain a tag')

    return urljoin(
        artifact_base_url(image_reference=image_reference),
        'manifests',
        tag,
    )


def _scope(image_reference: str, action: str):
    image_name = _image_name(image_reference=image_reference)
    # action = 'pull' # | pull,push | catalog
    scope = f'repository:{image_name}:{action}'
    return scope


class Client:
    def __init__(self, credentials_lookup: callable):
        self.credentials_lookup = credentials_lookup
        self.token_cache = OauthTokenCache()

    def _authenticate(
        self,
        image_reference: str,
        scope: str,
    ):
        if self.token_cache.token(scope=scope):
            return # no re-auth required, yet

        if scope == 'pull':
            privileges = oa.Privileges.READONLY,
        elif scope == 'push':
            privileges = oa.Privileges.READWRITE,
        else:
            privileges = None

        oci_creds = self.credentials_lookup(
            image_reference=image_reference,
            privileges=privileges,
            absent_ok=False,
        )

        url = base_api_url(image_reference=image_reference)
        res = requests.get(url)

        auth_challenge = www_authenticate.parse(res.headers['www-authenticate'])
        bearer = auth_challenge['bearer']
        service = bearer['service']

        realm = bearer['realm'] + '?' + urllib.parse.urlencode({
            'scope': scope,
            'service': service,
        })

        res = requests.get(
            realm,
            auth=requests.auth.HTTPBasicAuth(
              username=oci_creds.username,
              password=oci_creds.password,
            ),
        )

        token_dict = res.json()
        token_dict['scope'] = scope

        token = dacite.from_dict(
            data=token_dict,
            data_class=OauthToken,
        )

        self.token_cache.set_token(token)

    def _request(self, url: str, image_reference: str, scope: str, method: str='GET'):
        self._authenticate(
            image_reference=image_reference,
            scope=scope,
        )

        return requests.request(
            method=method,
            url=url,
            headers={
              'Authorization': f'Bearer {self.token_cache.token(scope=scope)}',
            },
        )

    def manifest_raw(self, image_reference: str):
        scope = _scope(image_reference=image_reference, action='pull')

        res = self._request(
            url=manifest_url(image_reference=image_reference),
            image_reference=image_reference,
            scope=scope,
        )

        return res

    def manifest(self, image_reference: str):
        res = self.manifest_raw(image_reference=image_reference)

        return dacite.from_dict(
            data_class=om.OciImageManifest,
            data=res.json(),
        )
