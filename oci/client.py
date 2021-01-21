import dataclasses
import datetime
import hashlib
import logging
import typing

import dacite
import dateutil.parser
import requests
import requests.auth
import urllib.parse
import urllib
import www_authenticate

import oci.auth as oa
import oci.model as om
import oci.util

urljoin = oci.util.urljoin

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class OauthToken:
    expires_in: int
    token: str
    scope: str
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


class OauthTokenCache:
    def __init__(self):
        self.tokens = {} # {scope: token}

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


def blobs_url(image_reference: str) -> str:
    return urljoin(
        artifact_base_url(image_reference),
        'blobs',
    )


def ls_tags_url(image_reference: str) -> str:
    return urljoin(
        artifact_base_url(image_reference),
        'tags',
        'list',
    )


def uploads_url(image_reference: str) -> str:
    return urljoin(
        blobs_url(image_reference),
        'uploads',
    ) + '/'


def put_blob_url(image_reference: str, digest: str) -> str:
    query = urllib.parse.urlencode({
        'digest': digest,
    })
    return uploads_url(image_reference=image_reference) + '?' + query


def blob_url(image_reference: str, digest: str):
    return urljoin(
        blobs_url(image_reference=image_reference),
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
        self.session = requests.Session()

    def _authenticate(
        self,
        image_reference: str,
        scope: str,
    ):
        if self.token_cache.token(scope=scope):
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
            logger.info(f'no credentials for {image_reference=} - attempting anonymous-auth')

        url = base_api_url(image_reference=image_reference)
        res = self.session.get(url)

        auth_challenge = www_authenticate.parse(res.headers['www-authenticate'])
        bearer = auth_challenge['bearer']
        service = bearer['service']

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
            realm,
            auth=auth,
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
        **kwargs,
    ):
        self._authenticate(
            image_reference=image_reference,
            scope=scope,
        )
        headers = headers or {}

        try:
            import ccc.elasticsearch
            import traceback
            ccc.elasticsearch.dump_elastic_search_document(
                es_config_name='sap_internal',
                index='component_descriptor_pull',
                body={
                    'method': method,
                    'url': url,
                    'stacktrace': traceback.format_stack(),
                }
            )
        except:
            import traceback
            logger.error(traceback.format_exc())
            logger.error('could not send elastic search dump')

        res = requests.request(
            method=method,
            url=url,
            headers={
              'Authorization': f'Bearer {self.token_cache.token(scope=scope).token}',
              **headers,
            },
            **kwargs,
        )
        if not res.ok:
            logger.warning(
                f'rq against {url=} failed {res.status_code=} {res.reason=} {method=} {res.content}'
            )

        if raise_for_status:
            res.raise_for_status()

        return res

    def manifest_raw(
        self,
        image_reference: str,
        absent_ok: bool=False,
    ):
        scope = _scope(image_reference=image_reference, action='pull')

        try:
            res = self._request(
                url=manifest_url(image_reference=image_reference),
                image_reference=image_reference,
                scope=scope,
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
    ):
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
                    url=blob_url(image_reference=image_reference, digest=digest),
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
            url=manifest_url(image_reference=image_reference),
            image_reference=image_reference,
            method='HEAD',
            scope=scope,
            stream=False,
            raise_for_status=not absent_ok,
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

    def tags(self, image_reference: str):
        scope = _scope(image_reference=image_reference, action='pull')

        res = self._request(
            url=ls_tags_url(image_reference=image_reference),
            image_reference=image_reference,
            scope=scope,
            method='GET'
        )

        return res.json()['tags']

    def put_manifest(self, image_reference: str, manifest: bytes):
        scope = _scope(image_reference=image_reference, action='push,pull')

        res = self._request(
            url=manifest_url(image_reference=image_reference),
            image_reference=image_reference,
            scope=scope,
            method='PUT',
            data=manifest,
        )

        res.raise_for_status()

    def blob(
        self,
        image_reference: str,
        digest: str,
        stream=True,
        absent_ok=False,
    ) -> requests.models.Response:
        scope = _scope(image_reference=image_reference, action='pull')

        res = self._request(
            url=blob_url(image_reference=image_reference, digest=digest),
            image_reference=image_reference,
            scope=scope,
            method='GET',
            stream=stream,
            timeout=None,
        )

        if absent_ok and res.status_code == requests.codes.NOT_FOUND:
            return None
        res.raise_for_status()

        return res

    def put_blob(
        self,
        image_reference: str,
        digest: str,
        octets_count: int,
        data: requests.models.Response,
        max_chunk=1024 * 1024 * 1, # 1 MiB
    ):
        data_is_requests_response = isinstance(data, requests.models.Response)
        data_is_generator = isinstance(data, typing.Generator)
        data_is_filelike = hasattr(data, 'read')

        if octets_count < max_chunk or data_is_filelike:
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
            url=uploads_url(image_reference=image_reference),
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

        res = self._request(
            url=put_blob_url(
                image_reference=image_reference,
                digest=digest,
            ),
            image_reference=image_reference,
            scope=scope,
            method='POST',
            headers={
                'content-type': 'application/octet-stream',
                'content-length': str(octets_count),
            },
            data=data,
            raise_for_status=False,
        )

        res.raise_for_status()
