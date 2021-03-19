import base64
import dataclasses
import datetime
import enum
import hashlib
import json
import logging
import typing

import dacite
import dateutil.parser
import requests
import requests.auth
import urllib
import urllib.parse
import www_authenticate

import oci.auth as oa
import oci.model as om
import oci.util

urljoin = oci.util.urljoin

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

oci_request_logger = logging.getLogger('oci.client.request_logger')
oci_request_logger.setLevel(logging.DEBUG)


def _append_b64_padding_if_missing(b64_str: str):
    if b64_str[-1] == '=':
        return b64_str

    if (mod4 := len(b64_str) % 4) == 2:
        return b64_str + '=' * 2
    elif mod4 == 3:
        return b64_str + '='
    elif mod4 == 0:
        return b64_str
    else:
        raise ValueError('this is a bug')


class AuthMethod(enum.Enum):
    BEARER = 'bearer'
    BASIC = 'basic'


@dataclasses.dataclass
class OauthToken:
    token: str
    scope: str
    expires_in: int = None
    issued_at: str = None

    def valid(self):
        issued_at = dateutil.parser.isoparse(self.issued_at)
        # pessimistically deduct 30s, to be on the safe side
        expiry_date = issued_at + datetime.timedelta(seconds=self.expires_in - 30)

        now = datetime.datetime.now(tz=datetime.timezone.utc)
        return now < expiry_date

    def __post_init__(self):
        if not self.issued_at:
            self.issued_at = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()
        if not self.expires_in:
            payload = self.token.split('.')[1]
            # add padding (JWT by convention has unpadded base64)
            payload = _append_b64_padding_if_missing(b64_str=payload)

            if (mod3 := len(payload) % 3) == 0:
                pass
            elif mod3 == 1:
                payload += '=='
            elif mod3 == 2:
                payload += '='

            parsed = json.loads(base64.b64decode(payload.encode('utf-8')))

            exp = parsed['exp']
            iat = parsed['iat']

            self.expires_in = exp - iat
            self.issued_at = datetime.datetime.fromtimestamp(iat, tz=datetime.timezone.utc)\
                .isoformat()


class OauthTokenCache:
    def __init__(self):
        self.tokens = {} # {scope: token}
        self.auth_methods = {} # {netloc: method}

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

    def set_auth_method(self, image_reference: str, auth_method: AuthMethod):
        netloc = parse_image_reference(image_reference=image_reference).netloc
        self.auth_methods[netloc] = auth_method

    def auth_method(self, image_reference: str) -> typing.Optional[AuthMethod]:
        netloc = parse_image_reference(image_reference=image_reference).netloc
        return self.auth_methods.get(netloc)


def parse_image_reference(image_reference: str):
    image_reference = oci.util.normalise_image_reference(image_reference)
    if not image_reference.startswith('https://'):
        image_reference = 'https://' + image_reference

    parsed_url = urllib.parse.urlparse(image_reference)
    return parsed_url


def _split_image_reference(image_reference: str):
    image_reference = oci.util.normalise_image_reference(image_reference)

    image_url = parse_image_reference(image_reference=image_reference)
    image_name_and_tag = image_url.path.lstrip('/')

    prefix = image_url.netloc

    if '@' in image_name_and_tag:
        image_name, image_tag = image_name_and_tag.rsplit('@', 1)
    else:
        image_name, image_tag = image_name_and_tag.rsplit(':', 1)

    return prefix, image_name, image_tag


def _image_name(image_reference: str):
    if ':' in image_reference or '@' in image_reference:
        return _split_image_reference(image_reference=image_reference)[1]
    else:
        if not image_reference.startswith('https://'):
            image_reference = 'https://' + image_reference
        parsed = urllib.parse.urlparse(image_reference)
        return parsed.path.lstrip('/')


def base_api_url(
    image_reference: str,
) -> str:
    parsed_url = parse_image_reference(image_reference=image_reference)
    base_url = f'https://{parsed_url.netloc}'

    return urljoin(base_url, 'v2') + '/'


class OciRoutes:
    def __init__(
        self,
        base_api_url_lookup: typing.Callable[[str], str]=base_api_url,
    ):
        self.base_api_url_lookup = base_api_url_lookup

    def artifact_base_url(
        self,
        image_reference: str,
    ) -> str:
        image_name = _image_name(image_reference=image_reference)

        return urljoin(
            self.base_api_url_lookup(image_reference=image_reference),
            image_name,
        )

    def _blobs_url(self, image_reference: str) -> str:
        return urljoin(
            self.artifact_base_url(image_reference),
            'blobs',
        )

    def ls_tags_url(self, image_reference: str) -> str:
        return urljoin(
            self.artifact_base_url(image_reference),
            'tags',
            'list',
        )

    def uploads_url(self, image_reference: str) -> str:
        return urljoin(
            self._blobs_url(image_reference),
            'uploads',
        ) + '/'

    def single_post_blob_url(self, image_reference: str, digest: str) -> str:
        '''
        used for "single-post" monolithic upload (not supported e.g. by registry-1.docker.io)
        '''
        query = urllib.parse.urlencode({
            'digest': digest,
        })
        return self.uploads_url(image_reference=image_reference) + '?' + query

    def blob_url(self, image_reference: str, digest: str):
        return urljoin(
            self._blobs_url(image_reference=image_reference),
            digest
        )

    def manifest_url(self, image_reference: str) -> str:
        last_part = image_reference.split('/')[-1]
        if '@' in last_part:
            tag = last_part.split('@')[-1]
        elif ':' in last_part:
            tag = last_part.split(':')[-1]
        else:
            raise ValueError(f'{image_reference=} does not seem to contain a tag')

        return urljoin(
            self.artifact_base_url(image_reference=image_reference),
            'manifests',
            tag,
        )


def _scope(image_reference: str, action: str):
    image_name = _image_name(image_reference=image_reference)
    # action = 'pull' # | pull,push | catalog
    scope = f'repository:{image_name}:{action}'
    return scope


class Client:
    def __init__(
        self,
        credentials_lookup: typing.Callable,
        routes: OciRoutes=OciRoutes(),
        disable_tls_validation=False,
    ):
        self.credentials_lookup = credentials_lookup
        self.token_cache = OauthTokenCache()
        self.session = requests.Session()
        self.routes = routes
        self.disable_tls_validation = disable_tls_validation

    def _authenticate(
        self,
        image_reference: str,
        scope: str,
    ):
        cached_auth_method = self.token_cache.auth_method(image_reference=image_reference)
        if cached_auth_method is AuthMethod.BASIC:
            return # basic-auth does not require any additional preliminary steps
        if cached_auth_method is AuthMethod.BEARER and self.token_cache.token(scope=scope):
            return # no re-auth required, yet

        if 'push' in scope:
            privileges = oa.Privileges.READWRITE
        elif 'pull' in scope:
            privileges = oa.Privileges.READONLY
        else:
            privileges = None

        oci_creds = self.credentials_lookup(
            image_reference=image_reference,
            privileges=privileges,
            absent_ok=True,
        )

        if not oci_creds:
            logger.warning(f'no credentials for {image_reference=} - attempting anonymous-auth')

        url = base_api_url(
            image_reference=image_reference,
        )
        res = self.session.get(
            url=url,
            verify=not self.disable_tls_validation,
        )

        auth_challenge = www_authenticate.parse(res.headers.get('www-authenticate'))

        # XXX HACK HACK: fallback to basic-auth if endpoints does not state what it wants
        if 'basic' in auth_challenge or not auth_challenge:
            self.token_cache.set_auth_method(
                image_reference=image_reference,
                auth_method=AuthMethod.BASIC,
            )
            return # no additional preliminary steps required for basic-auth
        elif 'bearer' in auth_challenge:
            bearer = auth_challenge['bearer']
            service = bearer['service']
            self.token_cache.set_auth_method(
                image_reference=image_reference,
                auth_method=AuthMethod.BEARER,
            )
        else:
            logger.warning(f'did not understand {auth_challenge=} - pbly a bug')

        realm = bearer['realm'] + '?' + urllib.parse.urlencode({
            'scope': scope,
            'service': service,
        })

        if oci_creds:
            auth = requests.auth.HTTPBasicAuth(
              username=oci_creds.username,
              password=oci_creds.password,
            )
        else:
            auth = None

        res = self.session.get(
            url=realm,
            verify=not self.disable_tls_validation,
            auth=auth,
        )

        if not res.ok:
            logger.warning(
                f'rq against {realm=} failed: {res.status_code=} {res.reason=} {res.content=}'
            )

        res.raise_for_status()

        token_dict = res.json()
        token_dict['scope'] = scope

        token = dacite.from_dict(
            data=token_dict,
            data_class=OauthToken,
        )

        self.token_cache.set_token(token)

    def _request(
        self,
        url: str,
        image_reference: str,
        scope: str,
        method: str='GET',
        headers: dict=None,
        raise_for_status=True,
        warn_if_not_ok=True,
        **kwargs,
    ):
        self._authenticate(
            image_reference=image_reference,
            scope=scope,
        )
        headers = headers or {}
        headers['User-Agent'] = 'gardener-oci (python3; github.com/gardener/cc-utils)'
        auth_method = self.token_cache.auth_method(image_reference=image_reference)
        auth = None

        if auth_method is AuthMethod.BASIC:
            actions = scope.split(':')[-1]
            if 'push' in actions:
                privileges = oa.Privileges.READWRITE
            else:
                privileges = oa.Privileges.READONLY

            if oci_creds := self.credentials_lookup(
                image_reference=image_reference,
                privileges=privileges,
                absent_ok=True,
            ):
                auth = oci_creds.username, oci_creds.password
            else:
                logger.warning(f'did not find any matching credentials for {image_reference=}')
        else:
            headers = {
              'Authorization': f'Bearer {self.token_cache.token(scope=scope).token}',
              **headers,
            }

        if self.disable_tls_validation and 'verify' in kwargs:
            kwargs['verify'] = False

        oci_request_logger.debug(
            msg=f'oci request sent {method=} {url=}',
            extra={
                'method': method,
                'url': url,
                'auth': auth,
                'headers': headers,
                **kwargs,
            },
        )

        res = requests.request(
            method=method,
            url=url,
            auth=auth,
            headers=headers,
            **kwargs,
        )
        if not res.ok and warn_if_not_ok:
            logger.warning(
                f'rq against {url=} failed {res.status_code=} {res.reason=} {method=} {res.content}'
            )

        if raise_for_status:
            if res.status_code != 404 and not res.ok:
                logger.debug(f'{url=} {res.content=} {res.headers=}')
            res.raise_for_status()

        return res

    def manifest_raw(
        self,
        image_reference: str,
        absent_ok: bool=False,
    ):
        scope = _scope(image_reference=image_reference, action='pull')

        # be backards-compatible, and also accept (legacy) docker-mimetype
        accept = f'{om.OCI_MANIFEST_SCHEMA_V2_MIME}, {om.DOCKER_MANIFEST_SCHEMA_V2_MIME}'

        try:
            res = self._request(
                url=self.routes.manifest_url(image_reference=image_reference),
                image_reference=image_reference,
                scope=scope,
                headers={
                    'Accept': accept,
                },
            )
        except requests.exceptions.HTTPError as he:
            if he.response.status_code == 404:
                if absent_ok:
                    return None
            raise om.OciImageNotFoundException(he.response) from he

        return res

    def manifest(
        self,
        image_reference: str,
        absent_ok: bool=False,
    ) -> om.OciImageManifest:
        res = self.manifest_raw(
            image_reference=image_reference,
            absent_ok=absent_ok,
        )

        if not res and absent_ok:
            return None

        manifest_dict = res.json()

        if (schema_version := int(manifest_dict['schemaVersion'])) == 1:
            manifest = dacite.from_dict(
                data_class=om.OciImageManifestV1,
                data=manifest_dict,
            )
            scope = _scope(image_reference=image_reference, action='pull')

            def fs_layer_to_oci_blob_ref(fs_layer: om.OciBlobRefV1):
                digest = fs_layer.blobSum

                res = self._request(
                    url=self.routes.blob_url(image_reference=image_reference, digest=digest),
                    image_reference=image_reference,
                    scope=scope,
                    method='HEAD',
                    stream=False,
                    timeout=None,
                )
                return om.OciBlobRef(
                    digest=digest,
                    mediaType=res.headers['Content-Type'],
                    size=int(res.headers['Content-Length']),
                )

            manifest.layers = [
                fs_layer_to_oci_blob_ref(fs_layer) for fs_layer
                in manifest.fsLayers
            ]

            return manifest
        elif schema_version == 2:
            return dacite.from_dict(
                data_class=om.OciImageManifest,
                data=manifest_dict,
            )
        else:
            raise NotImplementedError(schema_version)

    def head_manifest(
        self,
        image_reference: str,
        absent_ok=False,
    ) -> typing.Optional[om.OciBlobRef]:
        '''
        issues an HTTP-HEAD request for the specified oci-artifact's manifest and returns
        the thus-retrieved metadata if it exists.

        Note that the hash digest may be absent, or incorrect, as defined by the OCI
        distribution-spec.

        if `absent_ok` is truthy, `None` is returned in case the requested manifest does not
        exist; otherwise, requests.exceptions.HTTPError is raised in this case.

        To retrieve the actual manifest, use `self.manifest` or `self.manifest_raw`
        '''
        scope = _scope(image_reference=image_reference, action='pull')

        res = self._request(
            url=self.routes.manifest_url(image_reference=image_reference),
            image_reference=image_reference,
            method='HEAD',
            scope=scope,
            stream=False,
            raise_for_status=not absent_ok,
            warn_if_not_ok=not absent_ok,
        )
        if not res.ok and absent_ok:
            return None

        headers = res.headers

        # XXX Docker-Content-Digest header may be absent or incorrect
        # -> it would be preferrable to retrieve the manifest and calculate the hash manually

        return om.OciBlobRef(
            digest=headers.get('Docker-Content-Digest', None),
            mediaType=headers['Content-Type'],
            size=int(headers['Content-Length']),
        )

    def to_digest_hash(self, image_reference: str):
        # TODO: we might early-exit if img_ref already has a "hashtag"
        manifest_hash_digest = hashlib.sha256(
            self.manifest_raw(image_reference=image_reference).content
        ).hexdigest()
        prefix, image_name, _ = _split_image_reference(image_reference=image_reference)

        return f'{prefix}/{image_name}@sha256:{manifest_hash_digest}'

    def tags(self, image_reference: str):
        scope = _scope(image_reference=image_reference, action='pull')

        res = self._request(
            url=self.routes.ls_tags_url(image_reference=image_reference),
            image_reference=image_reference,
            scope=scope,
            method='GET'
        )

        return res.json()['tags']

    def put_manifest(
        self,
        image_reference: str,
        manifest: bytes,
    ):
        scope = _scope(image_reference=image_reference, action='push,pull')

        parsed = json.loads(manifest)
        content_type = parsed['mediaType']

        logger.info(f'manifest-mimetype: {content_type=}')

        res = self._request(
            url=self.routes.manifest_url(image_reference=image_reference),
            image_reference=image_reference,
            scope=scope,
            method='PUT',
            headers={
                'Content-Type': content_type,
            },
            data=manifest,
        )

        return res

    def delete_manifest(self, image_reference: str):
        scope = _scope(image_reference=image_reference, action='push,pull')

        res = self._request(
            url=self.routes.manifest_url(image_reference=image_reference),
            image_reference=image_reference,
            scope=scope,
            method='DELETE',
        )

        return res

    def blob(
        self,
        image_reference: str,
        digest: str,
        stream=True,
        absent_ok=False,
    ) -> requests.models.Response:
        scope = _scope(image_reference=image_reference, action='pull')

        res = self._request(
            url=self.routes.blob_url(image_reference=image_reference, digest=digest),
            image_reference=image_reference,
            scope=scope,
            method='GET',
            stream=stream,
            timeout=None,
            raise_for_status=False,
        )

        if absent_ok and res.status_code == requests.codes.NOT_FOUND:
            return None
        res.raise_for_status()

        return res

    def head_blob(
        self,
        image_reference: str,
        digest: str,
        absent_ok=True,
    ):
        scope = _scope(image_reference=image_reference, action='pull')

        res = self._request(
            url=self.routes.blob_url(
                image_reference=image_reference,
                digest=digest,
            ),
            method='HEAD',
            scope=scope,
            image_reference=image_reference,
            raise_for_status=False,
            warn_if_not_ok=not absent_ok,
        )

        if absent_ok and res.status_code == 404:
            return res

        res.raise_for_status()

        return res

    def put_blob(
        self,
        image_reference: str,
        digest: str,
        octets_count: int,
        data: requests.models.Response,
        max_chunk=1024 * 1024 * 1, # 1 MiB
        mimetype: str='application/data',
    ):
        head_res = self.head_blob(
            image_reference=image_reference,
            digest=digest,
        )
        if head_res.ok:
            logger.info(f'skipping blob upload {digest=} - already exists')
            return

        data_is_requests_response = isinstance(data, requests.models.Response)
        data_is_generator = isinstance(data, typing.Generator)
        data_is_filelike = hasattr(data, 'read')

        if octets_count < max_chunk or data_is_filelike or data_is_requests_response:
            if data_is_requests_response:
                data = data.content
            elif data_is_generator:
                # at least GCR does not like chunked-uploads; if small enough, workaround this
                # and create one (not-that-big) bytes-obj
                _data = bytes()
                for chunk in data:
                    _data += chunk
            elif data_is_filelike:
                pass # if filelike, http.client will handle streaming for us

            return self._put_blob_single_post(
                image_reference=image_reference,
                digest=digest,
                octets_count=octets_count,
                data=data,
            )
        else:
            if data_is_requests_response:
                with data:
                  return self._put_blob_chunked(
                      image_reference=image_reference,
                      digest=digest,
                      octets_count=octets_count,
                      data_iterator=data.iter_content(chunk_size=max_chunk),
                      chunk_size=max_chunk,
                  )
            elif data_is_generator:
              return self._put_blob_chunked(
                  image_reference=image_reference,
                  digest=digest,
                  octets_count=octets_count,
                  data_iterator=data.iter_content(chunk_size=max_chunk),
                  chunk_size=max_chunk,
              )
            else:
              raise NotImplementedError

    def _put_blob_chunked(
        self,
        image_reference: str,
        digest: str,
        octets_count: int,
        data_iterator: typing.Iterator[bytes],
        chunk_size: int=1024 * 1024 * 16, # 16 MiB
    ):
        scope = _scope(image_reference=image_reference, action='push,pull')
        logger.debug(f'chunked-put {chunk_size=}')

        # start uploading session
        res = self._request(
            url=self.routes.uploads_url(image_reference=image_reference),
            image_reference=image_reference,
            scope=scope,
            method='POST',
            headers={
                'content-length': '0',
            }
        )
        res.raise_for_status()

        upload_url = res.headers['location']

        octets_left = octets_count
        octets_sent = 0
        offset = 0
        sha256 = hashlib.sha256()

        while octets_left > 0:
            octets_to_send = min(octets_left, chunk_size)
            octets_left -= octets_to_send

            data = next(data_iterator)
            sha256.update(data)

            if not len(data) == octets_to_send:
                # sanity check to detect programming errors
                raise ValueError(f'{len(data)=} vs {octets_to_send=}')

            logger.debug(f'{octets_to_send=} {octets_left=} {len(data)=}')
            logger.debug(f'{octets_sent + offset}-{octets_sent + octets_to_send + offset}')

            crange_from = octets_sent
            crange_to = crange_from + len(data) - 1

            res = self._request(
                url=upload_url,
                image_reference=image_reference,
                scope=scope,
                method='PATCH',
                data=data,
                headers={
                 'Content-Length': str(octets_to_send),
                 'Content-Type': 'application/octet-stream',
                 'Content-Range': f'{crange_from}-{crange_to}',
                 'Range': f'{crange_from}-{crange_to}',
                }
            )
            res.raise_for_status()

            upload_url = res.headers['location']

            octets_sent += len(data)

        sha256_digest = f'sha256:{sha256.hexdigest()}'

        # close uploading session
        query = urllib.parse.urlencode({'digest': sha256_digest})
        upload_url = res.headers['location'] + '?' + query
        res = self._request(
            url=upload_url,
            image_reference=image_reference,
            scope=scope,
            method='PUT',
            headers={
                 'Content-Length': '0',
            },
        )
        return res

    def _put_blob_single_post(
        self,
        image_reference: str,
        digest: str,
        octets_count: int,
        data: bytes,
    ):
        logger.debug(f'single-post {image_reference=} {octets_count=}')
        scope = _scope(image_reference=image_reference, action='push,pull')

        # XXX according to distribution-spec, single-POST should also work - however
        # this seems not to be true for registry-1.docker.io. To keep the code simple(r),
        # always do a two-step upload; we might add a cfg-option (or maybe even discovery) for
        # using single-post uploads for registries that support it (such as GCR or artifactory)
        res = self._request(
            url=self.routes.uploads_url(
                image_reference=image_reference,
            ),
            image_reference=image_reference,
            scope=scope,
            method='POST',
        )

        upload_url = res.headers.get('Location')

        if '?' in upload_url:
            prefix = '&'
        else:
            prefix = '?'

        upload_url += prefix + urllib.parse.urlencode({'digest': digest})

        res = self._request(
            url=upload_url,
            image_reference=image_reference,
            scope=scope,
            method='PUT',
            headers={
                'content-type': 'application/octet-stream',
                'content-length': str(octets_count),
            },
            data=data,
            raise_for_status=False,
        )

        if not res.status_code == 201: # spec says it MUST be 201
            # also, 202 indicates the upload actually did not succeed e.g. for "docker-hub"
            logger.warning(f'{image_reference=} {res.status_code=} {digest=} - PUT may have failed')

        res.raise_for_status()
