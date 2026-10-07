import collections.abc
import dataclasses
import itertools
import logging
import os
import shutil
import tempfile

import aiohttp.client_exceptions
import cachetools
import dacite
import requests
import yaml

import ocm.diff
import ocm
import ocm.iter as oi
import ocm.oci
import ocm.retrieve
import oci.client_async as oca
import oci.model as om


logger = logging.getLogger(__name__)


ComponentDescriptorLookupById = collections.abc.Callable[
    [ocm.ComponentId, ocm.retrieve.OcmRepositoryLookup],
    collections.abc.Awaitable[ocm.ComponentDescriptor],
]

VersionLookupByComponent = collections.abc.Callable[
    [ocm.ComponentName, ocm.OcmRepository],
    collections.abc.Awaitable[collections.abc.Sequence[str]],
]


class WriteBack:
    '''
    Wraps an async writeback function which can be used to fill the lookup in case the required
    component descriptor was not found.
    '''
    def __init__(
        self,
        writeback: collections.abc.Callable[[ocm.ComponentIdentity, ocm.ComponentDescriptor], None],
    ):
        self.writeback = writeback

    async def __call__(
        self,
        component_id: ocm.ComponentIdentity,
        component_descriptor: ocm.ComponentDescriptor,
    ):
        await self.writeback(component_id, component_descriptor)


def in_memory_cache_component_descriptor_lookup(
    cache_ctor: cachetools.Cache=cachetools.LRUCache,
    ocm_repository_lookup: ocm.retrieve.OcmRepositoryLookup=None,
    **cache_kwargs,
) -> ComponentDescriptorLookupById:
    cache_kwargs['maxsize'] = cache_kwargs.get('maxsize', 2048)
    cache = cache_ctor(**cache_kwargs)

    async def writeback(
        component_id: ocm.ComponentIdentity,
        component_descriptor: ocm.ComponentDescriptor,
    ):
        if (ocm_repo := component_descriptor.component.current_ocm_repo):
            cache.__setitem__((component_id, ocm_repo), component_descriptor)
        else:
            raise ValueError(ocm_repo)

    _writeback = WriteBack(writeback)

    async def lookup(
        component_id: ocm.ComponentId,
        ocm_repository_lookup=ocm_repository_lookup,
    ):
        component_id = ocm.to_component_id(component_id)

        ocm_repos = ocm.retrieve.iter_ocm_repositories(
            component_id,
            ocm_repository_lookup,
        )

        for ocm_repo in ocm_repos:
            if isinstance(ocm_repo, str):
                ocm_repo = ocm.OciOcmRepository(
                    type=ocm.AccessType.OCI_REGISTRY,
                    baseUrl=ocm_repo,
                )
            try:
                if (component_descriptor := cache.get((component_id, ocm_repo))):
                    return component_descriptor
            except KeyError:
                pass

        # component descriptor not found in lookup
        return _writeback

    return lookup


def file_system_cache_component_descriptor_lookup(
    ocm_repository_lookup: ocm.retrieve.OcmRepositoryLookup=None,
    cache_dir: str=None,
) -> ComponentDescriptorLookupById:
    if not cache_dir:
        raise ValueError(cache_dir)

    async def writeback(
        component_id: ocm.ComponentIdentity,
        component_descriptor: ocm.ComponentDescriptor,
    ):
        if not (ocm_repo := component_descriptor.component.current_ocm_repo):
            raise ValueError(ocm_repo)

        try:
            f = tempfile.NamedTemporaryFile(mode='w', delete=False)
            # write to tempfile, followed by a mv to avoid collisions through concurrent
            # processes or threads (assuming mv is an atomic operation)
            yaml.dump(
                data=dataclasses.asdict(component_descriptor),
                Dumper=ocm.EnumValueYamlDumper,
                stream=f.file,
            )
            f.close() # need to close filehandle for NT

            descriptor_path = os.path.join(
                cache_dir,
                ocm_repo.oci_ref.replace('/', '-'),
                f'{component_id.name}-{component_id.version}',
            )
            if not os.path.isfile(descriptor_path):
                base_dir = os.path.dirname(descriptor_path)
                os.makedirs(name=base_dir, exist_ok=True)
            shutil.move(f.name, descriptor_path)
        except:
            os.unlink(f.name)
            raise

    _writeback = WriteBack(writeback)

    async def lookup(
        component_id: ocm.ComponentId,
        ocm_repository_lookup: ocm.retrieve.OcmRepositoryLookup=ocm_repository_lookup,
    ):
        component_id = ocm.to_component_id(component_id)

        ocm_repos = ocm.retrieve.iter_ocm_repositories(
            component_id,
            ocm_repository_lookup,
        )

        for ocm_repo in ocm_repos:
            if not ocm_repo:
                raise ValueError(ocm_repo)

            if isinstance(ocm_repo, str):
                ocm_repo = ocm.OciOcmRepository(
                    type=ocm.AccessType.OCI_REGISTRY,
                    baseUrl=ocm_repo,
                )

            if not isinstance(ocm_repo, ocm.OciOcmRepository):
                raise NotImplementedError(ocm_repo)

            descriptor_path = os.path.join(
                cache_dir,
                ocm_repo.oci_ref.replace('/', '-'),
                f'{component_id.name}-{component_id.version}',
            )
            if os.path.isfile(descriptor_path):
                with open(descriptor_path) as f:
                    return ocm.ComponentDescriptor.from_dict(
                        yaml.safe_load(f),
                    )

        # component descriptor not found in lookup
        return _writeback

    return lookup


def delivery_service_component_descriptor_lookup(
    ocm_repository_lookup: ocm.retrieve.OcmRepositoryLookup,
    delivery_client,
    default_absent_ok: bool=True,
    default_ignore_errors: tuple[Exception]=(
        requests.exceptions.ConnectionError,
        requests.exceptions.ReadTimeout,
    ),
    fallback_to_service_mapping: bool=True,
) -> ComponentDescriptorLookupById:
    if not delivery_client:
        raise ValueError(delivery_client)

    async def lookup(
        component_id: ocm.ComponentId,
        ocm_repository_lookup: ocm.retrieve.OcmRepositoryLookup=ocm_repository_lookup,
        absent_ok: bool=default_absent_ok,
        ignore_errors: tuple[Exception]=default_ignore_errors,
    ):
        component_id = ocm.to_component_id(component_id)

        ocm_repos = ocm.retrieve.iter_ocm_repositories(
            component_id,
            ocm_repository_lookup,
        )

        # if component descriptor is not found in `ocm_repos`, fallback to default ocm repo mapping
        # defined in delivery service (i.e. specify no ocm repository)
        if fallback_to_service_mapping:
            ocm_repos = itertools.chain(ocm_repos, (None,))

        for ocm_repo in ocm_repos:
            if isinstance(ocm_repo, str):
                ocm_repo = ocm.OciOcmRepository(
                    type=ocm.AccessType.OCI_REGISTRY,
                    baseUrl=ocm_repo,
                )

            if ocm_repo and not isinstance(ocm_repo, ocm.OciOcmRepository):
                raise NotImplementedError(ocm_repo)

            try:
                component_descriptor = delivery_client.component_descriptor(
                    name=component_id.name,
                    version=component_id.version,
                    ocm_repo_url=ocm_repo.oci_ref if ocm_repo else None,
                    ignore_lookup=ocm_repo is not None, # don't use lookup if `ocm_repo` is specified
                )

                if component_descriptor:
                    return component_descriptor
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 404:
                    continue
                elif e.response.status_code >= 500:
                    # in case delivery-service is not reachable, fallback to next lookup (if any)
                    return None
                raise
            except ignore_errors:
                # already return here to not use unintended "fallback" ocm repositories
                return None

        # component descriptor not found in lookup
        if absent_ok:
            return None
        raise om.OciImageNotFoundException

    return lookup


async def component_descriptor_from_oci(
    component_id: ocm.ComponentIdentity,
    ocm_repos: collections.abc.Iterable[ocm.OciOcmRepository | str],
    oci_client: oca.Client,
    absent_ok: bool=False,
) -> ocm.ComponentDescriptor | None:
    for ocm_repo in ocm_repos:
        if isinstance(ocm_repo, str):
            ocm_repo = ocm.OciOcmRepository(
                baseUrl=ocm_repo,
            )

        if not isinstance(ocm_repo, ocm.OciOcmRepository):
            raise NotImplementedError(ocm_repo)

        target_ref = '/'.join((
            ocm_repo.oci_ref.rstrip('/'),
            'component-descriptors',
            f'{component_id.name.lower()}:{component_id.version}', # oci-spec allows only lowercase
        ))

        manifest = await oci_client.manifest(
            image_reference=target_ref,
            absent_ok=True,
            accept=f'{om.OCI_MANIFEST_SCHEMA_V2_MIME}, {om.OCI_IMAGE_INDEX_MIME}',
        )

        if not manifest:
            continue

        if manifest.mediaType == om.OCI_IMAGE_INDEX_MIME:
            digest = ocm.oci.find_component_descriptor_manifest_digest(manifest)

            manifest = await oci_client.manifest(
                image_reference=om.OciImageReference(target_ref).with_tag(digest),
                accept=om.OCI_MANIFEST_SCHEMA_V2_MIME,
            )

        break
    else:
        manifest = None

    if not manifest and absent_ok:
        return None
    elif not manifest:
        raise om.OciImageNotFoundException

    try:
        cfg_blob = await oci_client.blob(
            image_reference=target_ref,
            digest=manifest.config.digest,
        )
        cfg_dict = await cfg_blob.json(
            content_type='application/octet-stream'
        )
        cfg = dacite.from_dict(
            data_class=ocm.oci.ComponentDescriptorOciCfg,
            data=cfg_dict,
        )
        layer_digest = cfg.componentDescriptorLayer.digest
        layer_mimetype = cfg.componentDescriptorLayer.mediaType
    except Exception as e:
        logger.warning(
            f'Failed to parse or retrieve component-descriptor-cfg: {e=}. '
            'falling back to single-layer'
        )

        # by contract, there must be exactly one layer (tar w/ component-descriptor)
        if (layers_count := len(manifest.layers)) != 1:
            logger.warning(f'XXX unexpected amount of {layers_count=}')

        layer_digest = manifest.layers[0].digest
        layer_mimetype = manifest.layers[0].mediaType

    blob = await oci_client.blob(
        image_reference=target_ref,
        digest=layer_digest,
    )
    component_descriptor_blob = await blob.content.read()

    return ocm.oci.component_descriptor_from_blob(
        component_descriptor_blob=component_descriptor_blob,
        layer_mimetype=layer_mimetype,
        target_ref=target_ref,
        component_id=component_id,
    )


def oci_component_descriptor_lookup(
    ocm_repository_lookup: ocm.retrieve.OcmRepositoryLookup=None,
    oci_client: oca.Client | collections.abc.Callable[[], oca.Client]=None,
    default_absent_ok=True,
) -> ComponentDescriptorLookupById:
    if not oci_client:
        raise ValueError(oci_client)

    async def lookup(
        component_id: ocm.ComponentId,
        ocm_repository_lookup: ocm.retrieve.OcmRepositoryLookup=ocm_repository_lookup,
        absent_ok=default_absent_ok,
    ):
        component_id = ocm.to_component_id(component_id)

        if isinstance(oci_client, collections.abc.Callable):
            local_oci_client = oci_client()
        else:
            local_oci_client = oci_client

        if not ocm_repository_lookup:
            raise ValueError('ocm_repository_lookup must be passed')

        ocm_repos = ocm.retrieve.iter_ocm_repositories(
            component_id,
            ocm_repository_lookup,
        )

        for ocm_repo in ocm_repos:
            if isinstance(ocm_repo, str):
                ocm_repo = ocm.OciOcmRepository(
                    baseUrl=ocm_repo,
                )

            if component_descriptor := await component_descriptor_from_oci(
                component_id=component_id,
                ocm_repos=(ocm_repo,),
                oci_client=local_oci_client,
                absent_ok=True,
            ):
                break
        else:
            component_descriptor = None

        if not component_descriptor and absent_ok:
            return
        elif not component_descriptor and not absent_ok:
            raise om.OciImageNotFoundException(component_id)

        # ensure OCM repository in which component descriptor was found is the current OCM repository
        component_descriptor.component.set_current_ocm_repo(ocm_repo)

        return component_descriptor

    return lookup


def version_lookup(
    ocm_repository_lookup: ocm.retrieve.OcmRepositoryLookup,
    oci_client: oca.Client,
    default_absent_ok: bool=True,
) -> VersionLookupByComponent:
    async def lookup(
        component_id: ocm.ComponentName,
        ocm_repository_lookup: ocm.retrieve.OcmRepositoryLookup=ocm_repository_lookup,
        absent_ok: bool=default_absent_ok,
    ):
        component_name = ocm.to_component_name(component_id)

        ocm_repos = ocm.retrieve.iter_ocm_repositories(
            component_name,
            ocm_repository_lookup,
        )

        versions = set()
        for ocm_repo in ocm_repos:
            if isinstance(ocm_repo, str):
                ocm_repo = ocm.OciOcmRepository(
                    type=ocm.OciAccess,
                    baseUrl=ocm_repo,
                )
            if not isinstance(ocm_repo, ocm.OciOcmRepository):
                raise NotImplementedError(ocm_repo)

            try:
                for version_tag in await component_versions(
                    component_name=component_name,
                    ocm_repo=ocm_repo,
                    oci_client=oci_client,
                ):
                    versions.add(version_tag)
            except aiohttp.client_exceptions.ClientResponseError as e:
                if (error_code := e.status) == 404:
                    continue

                image_reference = ocm_repo.component_oci_ref(component_name)
                if error_code == ocm.retrieve.error_code_indicating_not_found(
                    image_reference=image_reference,
                ):
                    continue

                raise

        if not versions and not absent_ok:
            raise om.OciImageNotFoundException()

        return versions

    return lookup


def composite_component_descriptor_lookup(
    lookups: tuple[ComponentDescriptorLookupById, ...],
    ocm_repository_lookup: ocm.retrieve.OcmRepositoryLookup | None=None,
    default_absent_ok=True,
) -> ComponentDescriptorLookupById:
    async def lookup(
        component_id: ocm.ComponentId,
        /,
        ocm_repository_lookup=ocm_repository_lookup,
        absent_ok=default_absent_ok,
    ):
        component_id = ocm.to_component_id(component_id)

        writebacks = []
        for lookup in lookups:
            res = None
            try:
                res = await lookup(
                    component_id,
                    ocm_repository_lookup=ocm_repository_lookup,
                )
            except om.OciImageNotFoundException:
                pass
            except dacite.DaciteError as ce:
                ce.add_note(f'{component_id=}')
                raise ce
            except requests.exceptions.HTTPError as he:
                if he.response.status_code != 500:
                    raise
                logger.warning(f'caught error {he} in {lookup=}, will try next lookup if any')

            if isinstance(res, ocm.ComponentDescriptor):
                for wb in writebacks: await wb(component_id, res)
                return res
            elif res is None: continue
            elif isinstance(res, WriteBack): writebacks.append(res)

        # component descriptor not found in lookup
        if absent_ok:
            return

        if ocm_repository_lookup:
            def to_repo_url(ocm_repo):
                if isinstance(ocm_repo, str):
                    return ocm_repo
                else:
                    return ocm_repo.oci_ref

            ocm_repository_urls = '\n'.join(
                to_repo_url(ocm_repository) for ocm_repository
                in ocm_repository_lookup(component_id)
            )
            error = f'Did not find {component_id=} in any of the following\n'
            error += f'ocm-repositories:\n{ocm_repository_urls}:\n{str(component_id)}'
        else:
            error = f'<no ocm-repo given>: {str(component_id)}'

        raise om.OciImageNotFoundException(
            error,
        )

    return lookup


def create_default_component_descriptor_lookup(
    ocm_repository_lookup: ocm.retrieve.OcmRepositoryLookup,
    cache_dir: str | None=None,
    oci_client: oca.Client | collections.abc.Callable[[], oca.Client]=None,
    delivery_client=None,
    default_absent_ok: bool=False,
    fallback_to_service_mapping: bool=True,
) -> ComponentDescriptorLookupById:
    lookups = [
        in_memory_cache_component_descriptor_lookup(
            ocm_repository_lookup=ocm_repository_lookup,
        )
    ]
    if cache_dir:
        lookups.append(
            file_system_cache_component_descriptor_lookup(
                cache_dir=cache_dir,
                ocm_repository_lookup=ocm_repository_lookup,
            )
        )

    if delivery_client:
        lookups.append(
            delivery_service_component_descriptor_lookup(
                delivery_client=delivery_client,
                ocm_repository_lookup=ocm_repository_lookup,
                fallback_to_service_mapping=fallback_to_service_mapping,
            )
        )

    lookups.append(
        oci_component_descriptor_lookup(
            oci_client=oci_client,
            ocm_repository_lookup=ocm_repository_lookup,
        ),
    )

    return composite_component_descriptor_lookup(
        lookups=tuple(lookups),
        ocm_repository_lookup=ocm_repository_lookup,
        default_absent_ok=default_absent_ok,
    )


async def component_diff(
    left_component: ocm.Component | ocm.ComponentDescriptor,
    right_component: ocm.Component | ocm.ComponentDescriptor,
    component_descriptor_lookup: ComponentDescriptorLookupById,
    ignore_component_names=(),
) -> ocm.diff.ComponentDiff:
    import ocm.iter_async as oia # late import to avoid cyclic dependencies

    left_component = ocm.to_component(left_component)
    right_component = ocm.to_component(right_component)

    left_components = [
        component_node.component async for component_node in oia.iter(
            component=left_component,
            lookup=component_descriptor_lookup,
            node_filter=oi.Filter.components,
        ) if component_node.component.name not in ignore_component_names
    ]

    right_components = [
        component_node.component async for component_node in oia.iter(
            component=right_component,
            lookup=component_descriptor_lookup,
            node_filter=oi.Filter.components,
        ) if component_node.component.name not in ignore_component_names
    ]

    return ocm.diff.diff_components(
        left_components=left_components,
        right_components=right_components,
        ignore_component_names=ignore_component_names,
    )


async def component_versions(
    component_name: str,
    ocm_repo: ocm.OcmRepository,
    oci_client: oca.Client,
) -> collections.abc.Sequence[str]:
    if not isinstance(ocm_repo, ocm.OciOcmRepository):
        raise NotImplementedError(ocm_repo)

    ocm_repo: ocm.OciOcmRepository
    oci_ref = ocm_repo.component_oci_ref(component_name)

    return await oci_client.tags(image_reference=oci_ref)
