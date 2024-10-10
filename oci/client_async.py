import asyncio
import collections.abc
import hashlib
import io
import json
import logging
import tempfile

import aiohttp
import aiohttp.client_exceptions
import dacite
import urllib.parse
import www_authenticate

import oci.auth as oa
import oci.client
import oci.model as om


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

oci_request_logger = logging.getLogger('oci.client.request_logger')
oci_request_logger.setLevel(logging.DEBUG)


class Client:
    def __init__(
        self,
        credentials_lookup: collections.abc.Callable,
        routes: oci.client.OciRoutes=oci.client.OciRoutes(),
        disable_tls_validation: bool=False,
        timeout_seconds: int=None,
        session: aiohttp.ClientSession=None,
        tag_preprocessing_callback: collections.abc.Callable[[str], str]=None,
        tag_postprocessing_callback: collections.abc.Callable[[str], str]=None,
    ):
        '''
        @param credentials_lookup <Callable>
        @param routes <OciRoutes>
        @param disable_tls_validation <bool>
        @param timeout_seconds <int>
        @param session <ClientSession>
        @param tag_preprocessing_callback <Callable>
            callback which is instrumented _prior_ to interacting with the OCI registry, i.e. useful
            in case the tag has to be sanitised so it is accepted by the OCI registry
        @param tag_postprocessing_callback <Callable>
            callback which is instrumented _after_ interacting with the OCI registry, i.e. useful to
            revert required sanitisation of `tag_preprocessing_callback`
        '''
        self.credentials_lookup = credentials_lookup
        self.token_cache = oci.client.OauthTokenCache()
        if not session:
            self.session = aiohttp.ClientSession()
        else:
            self.session = session
        self.routes = routes
        self.disable_tls_validation = disable_tls_validation
        self.tag_preprocessing_callback = tag_preprocessing_callback
        self.tag_postprocessing_callback = tag_postprocessing_callback

        if timeout_seconds:
            timeout_seconds = int(timeout_seconds)
        self.timeout_seconds = timeout_seconds

    async def _authenticate(
        self,
        image_reference: str | om.OciImageReference,
        scope: str,
        remaining_retries: int=3,
    ):
        if isinstance(image_reference, om.OciImageReference):
            image_reference = str(image_reference)

        cached_auth_method = self.token_cache.auth_method(image_reference=image_reference)
        if cached_auth_method is oci.client.AuthMethod.BASIC:
            return # basic-auth does not require any additional preliminary steps
        if (
            cached_auth_method is oci.client.AuthMethod.BEARER
            and self.token_cache.token(scope=scope)
        ):
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
            logger.debug(f'no credentials for {image_reference=} - attempting anonymous-auth')

        url = oci.client.base_api_url(
            image_reference=str(image_reference),
        )

        res = await self.session.get(
            url=url,
            ssl=not self.disable_tls_validation,
            timeout=121,
        )

        auth_challenge = www_authenticate.parse(res.headers.get('www-authenticate'))

        # XXX HACK HACK: fallback to basic-auth if endpoints does not state what it wants
        if 'basic' in auth_challenge or not auth_challenge:
            self.token_cache.set_auth_method(
                image_reference=image_reference,
                auth_method=oci.client.AuthMethod.BASIC,
            )
            return # no additional preliminary steps required for basic-auth
        elif 'bearer' in auth_challenge:
            bearer = auth_challenge['bearer']
            service = bearer.get('service')
            self.token_cache.set_auth_method(
                image_reference=image_reference,
                auth_method=oci.client.AuthMethod.BEARER,
            )
        else:
            logger.warning(f'did not understand {auth_challenge=} - pbly a bug')

        bearer_dict = {'scope': scope}
        if service:
            bearer_dict['service'] = service

        realm = bearer['realm'] + '?' + urllib.parse.urlencode(bearer_dict)

        if oci_creds:
            auth = aiohttp.BasicAuth(
                login=oci_creds.username,
                password=oci_creds.password,
            )
        else:
            auth = None

        res = await self.session.get(
            url=realm,
            ssl=not self.disable_tls_validation,
            auth=auth,
            timeout=121,
        )

        if not res.ok:
            logger.warning(
                f'rq against {realm=} failed: {res.status=} {res.reason=} {await res.text()=}'
            )

            if res.status == 429 and remaining_retries > 0:
                logger.warning('quota was exceeded, will wait a minute and then retry again')
                await asyncio.sleep(60)
                return self._authenticate(
                    image_reference=image_reference,
                    scope=scope,
                    remaining_retries=remaining_retries - 1,
                )

        res.raise_for_status()

        token_dict = await res.json()
        token_dict['scope'] = scope

        token = dacite.from_dict(
            data=token_dict,
            data_class=oci.client.OauthToken,
        )

        self.token_cache.set_token(token)

    async def _request(
        self,
        url: str,
        image_reference: str | om.OciImageReference,
        scope: str,
        method: str='GET',
        headers: dict=None,
        raise_for_status=True,
        warn_if_not_ok=True,
        remaining_retries: int=3,
        **kwargs,
    ):
        if not 'timeout' in kwargs and self.timeout_seconds:
            kwargs['timeout'] = self.timeout_seconds

        image_reference = om.OciImageReference.to_image_ref(image_reference)

        try:
            await self._authenticate(
                image_reference=image_reference,
                scope=scope,
            )
        except aiohttp.client_exceptions.ClientResponseError as e:
            if remaining_retries == 0:
                raise

            logger.warning(f'caught response error, going to retry... ({remaining_retries=}); {e}')
            return await self._request(
                url=url,
                image_reference=image_reference,
                scope=scope,
                method=method,
                headers=headers,
                raise_for_status=raise_for_status,
                warn_if_not_ok=warn_if_not_ok,
                remaining_retries=remaining_retries - 1,
                **kwargs,
            )

        headers = headers or {}
        headers['User-Agent'] = 'gardener-oci (python3; github.com/gardener/cc-utils)'
        auth_method = self.token_cache.auth_method(image_reference=image_reference)
        auth = None

        if auth_method is oci.client.AuthMethod.BASIC:
            actions = scope.split(':')[-1]
            if 'push' in actions:
                privileges = oa.Privileges.READWRITE
            else:
                privileges = oa.Privileges.READONLY

            if oci_creds := self.credentials_lookup(
                image_reference=image_reference.original_image_reference,
                privileges=privileges,
                absent_ok=True,
            ):
                auth = aiohttp.BasicAuth(
                    login=oci_creds.username,
                    password=oci_creds.password,
                )
            else:
                logger.debug(f'did not find any matching credentials for {image_reference=}')
        else:
            headers = {
              'Authorization': f'Bearer {self.token_cache.token(scope=scope).token}',
              **headers,
            }

        if self.disable_tls_validation and 'ssl' in kwargs:
            kwargs['ssl'] = False

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

        try:
            timeout = kwargs.pop('timeout')
        except KeyError:
            timeout = 121

        try:
            res = await self.session.request(
                method=method,
                url=url,
                auth=auth,
                headers=headers,
                timeout=timeout,
                **kwargs,
            )
        except aiohttp.client_exceptions.ClientResponseError as e:
            if remaining_retries == 0:
                raise

            logger.warning(f'caught ConnectionError, going to retry... ({remaining_retries=}); {e}')
            return await self._request(
                url=url,
                image_reference=image_reference,
                scope=scope,
                method=method,
                headers=headers,
                raise_for_status=raise_for_status,
                warn_if_not_ok=warn_if_not_ok,
                remaining_retries=remaining_retries - 1,
                **kwargs,
            )

        if not res.ok and warn_if_not_ok:
            logger.warning(
                f'rq against {url=} failed {res.status=} {res.reason=} {method=} {await res.text()}'
            )

        if raise_for_status:
            if res.status != 404 and not res.ok:
                logger.debug(f'{url=} {await res.text()=} {res.headers=}')
            res.raise_for_status()

        return res

    async def manifest_raw(
        self,
        image_reference: str | om.OciImageReference,
        absent_ok: bool=False,
        accept: str=None,
    ):
        image_reference = om.OciImageReference.to_image_ref(image_reference)

        scope = oci.client._scope(image_reference=image_reference, action='pull')

        # be backards-compatible, and also accept (legacy) docker-mimetype
        if not accept:
            accept = f'{om.OCI_MANIFEST_SCHEMA_V2_MIME}, {om.DOCKER_MANIFEST_SCHEMA_V2_MIME}'

        try:
            res = await self._request(
                url=self.routes.manifest_url(
                    image_reference=image_reference,
                    tag_preprocessing_callback=self.tag_preprocessing_callback,
                ),
                image_reference=image_reference,
                scope=scope,
                warn_if_not_ok=not absent_ok,
                headers={
                    'Accept': accept,
                },
            )
        except aiohttp.client_exceptions.ClientResponseError as e:
            if e.status == 404:
                if absent_ok:
                    return None
                raise om.OciImageNotFoundException(e)
            raise e

        return res

    async def manifest(
        self,
        image_reference: str | om.OciImageReference,
        absent_ok: bool=False,
        accept: str=None,
    ) -> om.OciImageManifest | om.OciImageManifestList:
        '''
        returns the parsed OCI Manifest for the given image reference. If the optional `accept`
        argument is passed, the given value will be set as `Accept` HTTP Header when retrieving
        the manifest (defaults to
            application/vnd.oci.image.manifest.v1+json
            application/vnd.docker.distribution.manifest.v2+json
        , which requests a single Oci Image manifest, with a preference for the mimetype defined
        by OCI, and accepting docker's mimetype as a fallback)

        The following mimetype is also well-known:

            application/vnd.oci.image.manifest.v1+json

        If set, and the underlying OCI Artifact is a "multi-arch" artifact, than the returned
        value is (parsed into) a OciImageManifestList.

        see oci.model for both mimetype and model class definitions.

        Note that in case no `accept` header is set, the returned manifest type differs depending
        on the OCI Registry, if there actually is a multi-arch artifact.

        GCR is known to return a single OCI Image manifest (defaulting to GNU/Linux x86_64),
        whereas the registry backing quay.io will return a Manifest-List regardless of accept
        header.
        '''
        image_reference = om.OciImageReference.to_image_ref(image_reference)
        res = await self.manifest_raw(
            image_reference=image_reference,
            absent_ok=absent_ok,
            accept=accept,
        )

        if not res and absent_ok:
            return None

        manifest_dict = await res.json()

        if manifest_dict.get('mediaType') in (
            om.DOCKER_MANIFEST_LIST_MIME,
            om.OCI_IMAGE_INDEX_MIME,
        ):
            manifest = dacite.from_dict(
                data_class=om.OciImageManifestList,
                data=manifest_dict,
            )
            return manifest

        schema_version = int(manifest_dict['schemaVersion'])
        if schema_version != 2:
            # only support v2 for async operation
            raise NotImplementedError(schema_version)

        return dacite.from_dict(
            data_class=om.OciImageManifest,
            data=manifest_dict,
        )

    async def head_manifest(
        self,
        image_reference: str,
        absent_ok=False,
        accept: str=None,
    ) -> om.OciBlobRef | None:
        '''
        issues an HTTP-HEAD request for the specified oci-artifact's manifest and returns
        the thus-retrieved metadata if it exists.

        Note that the hash digest may be absent, or incorrect, as defined by the OCI
        distribution-spec.

        if `absent_ok` is truthy, `None` is returned in case the requested manifest does not
        exist; otherwise, aiohttp.client_exceptions.ClientResponseError is raised in this case.

        To retrieve the actual manifest, use `self.manifest` or `self.manifest_raw`
        '''
        scope = oci.client._scope(image_reference=image_reference, action='pull')

        if not accept:
            accept = om.MimeTypes.single_image

        res = await self._request(
            url=self.routes.manifest_url(
                image_reference=image_reference,
                tag_preprocessing_callback=self.tag_preprocessing_callback,
            ),
            image_reference=image_reference,
            method='HEAD',
            headers={
                'accept': accept,
            },
            scope=scope,
            raise_for_status=not absent_ok,
            warn_if_not_ok=not absent_ok,
        )
        if not res.ok and absent_ok:
            return None

        headers = res.headers

        # XXX Docker-Content-Digest header may be absent or incorrect
        # -> it would be preferrable to retrieve the manifest and calculate the hash manually

        if size := headers.get('Content-Length'):
            size = int(size)

        return om.OciBlobRef(
            digest=headers.get('Docker-Content-Digest', None),
            mediaType=headers['Content-Type'],
            size=size,
        )

    async def to_digest_hash(
        self,
        image_reference: str | om.OciImageReference,
        accept: str=None,
    ):
        image_reference = om.OciImageReference.to_image_ref(image_reference)
        if image_reference.has_digest_tag:
            return str(image_reference)

        manifest_raw = await self.manifest_raw(
            image_reference=image_reference,
            accept=accept,
        )
        manifest_content = await manifest_raw.content.read()
        manifest_hash_digest = hashlib.sha256(manifest_content).hexdigest()

        prefix = image_reference.ref_without_tag

        return f'{prefix}@sha256:{manifest_hash_digest}'

    async def tags(self, image_reference: str):
        scope = oci.client._scope(image_reference=image_reference, action='pull')

        res = await self._request(
            url=self.routes.ls_tags_url(image_reference=image_reference),
            image_reference=image_reference,
            scope=scope,
            method='GET'
        )

        res.raise_for_status()

        # Google-Artifact-Registry (maybe also others) will return http-200 + HTML in certain
        # error cases (e.g. if image_reference contains a pipe (|) character)
        try:
            tags_res = await res.json()
            tags = tags_res['tags']
        except json.decoder.JSONDecodeError as jde:
            if not (content_type := res.headers['Content-Type']) == 'application/json':
                jde.add_note(f'unexpected Content-Type: {content_type=}')

            jde.add_note(f'{image_reference=}')

            raise jde

        if self.tag_postprocessing_callback:
            tags = [
                self.tag_postprocessing_callback(tag)
                for tag in tags
            ]

        return tags

    async def has_multiarch(self, image_reference: str) -> bool:
        res = await self.head_manifest(
            image_reference=image_reference,
            absent_ok=True,
            accept=om.MimeTypes.multiarch,
        )
        if res:
            return True

        # sanity-check: at least single image must exist
        await self.head_manifest(
            image_reference=image_reference,
            absent_ok=False,
            accept=om.MimeTypes.single_image,
        )
        return False

    async def put_manifest(
        self,
        image_reference: str | om.OciImageReference,
        manifest: bytes,
    ):
        image_reference = om.OciImageReference.to_image_ref(image_reference)
        scope = oci.client._scope(image_reference=image_reference, action='push,pull')

        parsed = json.loads(manifest)
        content_type = parsed.get('mediaType', om.OCI_MANIFEST_SCHEMA_V2_MIME)

        logger.debug(f'manifest-mimetype: {content_type=}')

        res = await self._request(
            url=self.routes.manifest_url(
                image_reference=image_reference,
                tag_preprocessing_callback=self.tag_preprocessing_callback,
            ),
            image_reference=image_reference,
            scope=scope,
            method='PUT',
            raise_for_status=False,
            headers={
                'Content-Type': content_type,
            },
            data=manifest,
        )

        if not res.ok:
            logger.warning(f'our manifest was rejected (see below for more details): {manifest=}')
        res.raise_for_status()

        return res

    async def delete_manifest(
        self,
        image_reference: om.OciImageReference | str,
        purge: bool=False,
        accept: str=om.MimeTypes.prefer_multiarch,
    ):
        '''
        deletes the specified manifest. Depending on whether the passed image_reference contains
        a digest or a symbolic tag, the resulting semantics (and error cases) differs.

        If the image-reference contains a symbolic tag, the tag will be removed (a.k.a. untagged).
        The manifest will still be accessible, either through other tags, or through digest-tag.

        If the image-reference contains a digest, the manifest will be removed and no longer be
        accessible. However, this operation will fail if there are tags referencing the manifest.

        If `purge` is set to `True`, _and_ the image-reference contains a symbolic tag, then the
        manifest will be retrieved (to calculate the digest), then it will be untagged, then the
        manifest will be deleted. Note that the last operation may still fail if there are other
        tags referencing the same manifest.
        '''
        image_reference = om.OciImageReference(image_reference)
        scope = oci.client._scope(image_reference=image_reference, action='push,pull')

        if not purge or image_reference.has_digest_tag:
            if accept:
                headers = {'Accept': accept}
            else:
                headers = {}

            return await self._request(
                url=self.routes.manifest_url(
                    image_reference=image_reference,
                    tag_preprocessing_callback=self.tag_preprocessing_callback,
                ),
                image_reference=image_reference,
                scope=scope,
                headers=headers,
                method='DELETE',
            )
        elif image_reference.has_symbolical_tag:
            manifest_raw = await self.manifest_raw(
                image_reference=image_reference,
                accept=accept,
            )
            manifest_content = await manifest_raw.content.read()
            manifest_digest = f'sha256:{hashlib.sha256(manifest_content).hexdigest()}'

            res = await self.delete_manifest(
                image_reference=image_reference,
                purge=False,
                accept=accept,
            )
            res.raise_for_status()
            return await self.delete_manifest(
                image_reference=f'{image_reference.ref_without_tag}@{manifest_digest}',
                purge=False,
            )
        else:
            raise RuntimeError('this case should not occur (this is a bug)')

    async def delete_blob(
        self,
        image_reference: om.OciImageReference | str,
        digest: str,
    ):
        image_reference = om.OciImageReference(image_reference)
        scope = oci.client._scope(image_reference=image_reference, action='push,pull')

        res = await self._request(
            url=self.routes.blob_url(image_reference=image_reference, digest=digest),
            image_reference=image_reference,
            scope=scope,
            method='DELETE',
        )
        res.raise_for_status()
        return res

    async def blob(
        self,
        image_reference: str | om.OciImageReference,
        digest: str,
        absent_ok=False,
    ) -> aiohttp.ClientResponse | None:
        image_reference = om.OciImageReference(image_reference)

        scope = oci.client._scope(image_reference=image_reference, action='pull')

        res = await self._request(
            url=self.routes.blob_url(image_reference=image_reference, digest=digest),
            image_reference=image_reference,
            scope=scope,
            method='GET',
            timeout=None,
            raise_for_status=False,
        )

        if absent_ok and res.status == 404:
            return None
        res.raise_for_status()

        return res

    async def head_blob(
        self,
        image_reference: str | om.OciImageReference,
        digest: str,
        absent_ok=True,
    ):
        image_reference = om.OciImageReference(image_reference)
        scope = oci.client._scope(image_reference=image_reference, action='pull')

        res = await self._request(
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

        if absent_ok and res.status == 404:
            return res

        res.raise_for_status()

        return res

    async def put_blob(
        self,
        image_reference: str | om.OciImageReference,
        digest: str,
        octets_count: int,
        data: aiohttp.ClientResponse | collections.abc.Generator | bytes | io.IOBase,
        max_chunk=1024 * 1024 * 1, # 1 MiB
        mimetype: str='application/octet-stream',
    ):
        '''
        uploads blob as part of an image-upload as specified in oci-distribution-spec:
        https://github.com/opencontainers/distribution-spec/blob/main/spec.md#push

        mimetype should not be set to a different value than the default. It is exposed for
        users seeking lowlevel control.
        '''
        image_reference = om.OciImageReference(image_reference)
        head_res = await self.head_blob(
            image_reference=image_reference,
            digest=digest,
        )
        if head_res.ok:
            logger.debug(f'skipping blob upload {digest=} - already exists')
            return

        data_is_client_resp = isinstance(data, aiohttp.ClientResponse)
        data_is_generator = isinstance(data, collections.abc.Generator)
        data_is_filelike = hasattr(data, 'read')
        data_is_bytes = isinstance(data, bytes)

        if octets_count < max_chunk or data_is_bytes:
            if data_is_client_resp:
                data = await data.content.read()
            elif data_is_generator:
                # at least GCR does not like chunked-uploads; if small enough, workaround this
                # and create one (not-that-big) bytes-obj
                _data = bytes()
                for chunk in data:
                    _data += chunk
                data = _data

            return await self._put_blob_single_post(
                image_reference=image_reference,
                digest=digest,
                octets_count=octets_count,
                data=data,
                mimetype=mimetype,
            )
        elif (
            octets_count >= max_chunk
            and (data_is_generator or data_is_client_resp or data_is_filelike)
        ):
            # workaround: write into temporary file, as at least GCR does not implement
            # chunked-upload, and requests will not properly work w/ all generators
            # (in particular, it will not work w/ our "fake" on)
            with tempfile.TemporaryFile() as tf:
                if data_is_generator:
                    for chunk in data:
                        tf.write(chunk)
                elif data_is_client_resp:
                    async for chunk in data.content.iter_chunked(4096):
                        tf.write(chunk)
                elif data_is_filelike:
                    while chunk := data.read(4096):
                        tf.write(chunk)
                else:
                    raise RuntimeError('this line must not be reached')
                tf.seek(0)

                return await self._put_blob_single_post(
                    image_reference=image_reference,
                    digest=digest,
                    octets_count=octets_count,
                    data=tf,
                    mimetype=mimetype,
                )
        else:
            raise NotImplementedError

    async def _put_blob_single_post(
        self,
        image_reference: str | om.OciImageReference,
        digest: str,
        octets_count: int,
        data: bytes,
        mimetype: str='application/octet-stream',
    ):
        logger.debug(f'single-post {image_reference=} {octets_count=}')
        image_reference = om.OciImageReference(image_reference)
        scope = oci.client._scope(image_reference=image_reference, action='push,pull')

        # XXX according to distribution-spec, single-POST should also work - however
        # this seems not to be true for registry-1.docker.io. To keep the code simple(r),
        # always do a two-step upload; we might add a cfg-option (or maybe even discovery) for
        # using single-post uploads for registries that support it (such as GCR or artifactory)
        res = await self._request(
            url=self.routes.uploads_url(
                image_reference=image_reference,
            ),
            image_reference=image_reference,
            scope=scope,
            method='POST',
        )

        upload_url = res.headers.get('Location')

        # returned url _may_ be relative
        if upload_url.startswith('/'):
            parsed_url = urllib.parse.urlparse(res.url)
            upload_url = f'{parsed_url.scheme}://{parsed_url.netloc}{upload_url}'

        if '?' in upload_url:
            prefix = '&'
        else:
            prefix = '?'

        upload_url += prefix + urllib.parse.urlencode({'digest': digest})

        res = await self._request(
            url=upload_url,
            image_reference=image_reference,
            scope=scope,
            method='PUT',
            headers={
                'content-type': mimetype,
                'content-length': str(octets_count),
            },
            data=data,
            raise_for_status=False,
        )

        if res.ok and not res.status == 201: # spec says it MUST be 201
            # also, 202 indicates the upload actually did not succeed e.g. for "docker-hub"
            logger.warning(f'{image_reference=} {res.status=} {digest=} - PUT may have failed')

        res.raise_for_status()
        return res
