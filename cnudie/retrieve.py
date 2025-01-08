import collections.abc
import dataclasses
import io
import itertools
import json
import logging
import os
import shutil
import tarfile
import tempfile

import requests
import yaml

import cachetools
import dacite
import ocm
import ocm.oci

import ci.util
import cnudie.util
import oci.client as oc
import oci.model as om


logger = logging.getLogger(__name__)

ComponentName = str | tuple[str, str] | ocm.Component | ocm.ComponentIdentity

VersionLookupByComponent = collections.abc.Callable[
    [ComponentName, ocm.OcmRepository],
    collections.abc.Sequence[str]
]


@dataclasses.dataclass(frozen=True)
class OcmRepositoryMappingEntry:
    repository: str
    prefix: str | None = None


OcmRepositoryCfg = str | collections.abc.Iterable[str]


OcmRepositoryLookup = collections.abc.Callable[
    [ComponentName],
    collections.abc.Generator[ocm.OciOcmRepository | str, None, None],
]

ComponentDescriptorLookupById = collections.abc.Callable[
    [ocm.ComponentIdentity, OcmRepositoryLookup],
    ocm.ComponentDescriptor
]


def _iter_ocm_repositories(
    component: str | ocm.ComponentIdentity | ocm.Component,
    repository_cfg: OcmRepositoryCfg,
    /,
):
    if isinstance(component, ocm.ComponentIdentity):
        component = component.name
    elif isinstance(component, ocm.Component):
        component = component.name

    if isinstance(repository_cfg, ocm.OciOcmRepository):
        yield repository_cfg
        return

    if isinstance(repository_cfg, str):
        yield repository_cfg
        return

    # recurse into elements in case repository_cfg is iterable
    if hasattr(repository_cfg, '__iter__'):
        for cfg in repository_cfg:
            yield _iter_ocm_repositories(component, cfg)
        return


def iter_ocm_repositories(
    component: str | ocm.ComponentIdentity | ocm.Component,
    /,
    *repository_cfgs: OcmRepositoryCfg,
) -> collections.abc.Generator[ocm.OciOcmRepository | str, None, None]:
    for repository_cfg in repository_cfgs:
        if not repository_cfg:
            # convenience: ignore, e.g. None
            # handy for passing-in multiple values, from which some may be none
            continue
        if callable(repository_cfg):
            for cfg in repository_cfg(component):
                if not cfg:
                    continue
                yield cfg
            return

        for cfg in _iter_ocm_repositories(component, repository_cfg):
            if hasattr(cfg, '__iter__'):
                yield from cfg
            else:
                yield cfg


def ocm_repository_lookup(*repository_cfgs: OcmRepositoryCfg):
    def lookup(
        component: str | ocm.ComponentIdentity | ocm.Component,
    ):
        return iter_ocm_repositories(component, repository_cfgs)

    return lookup


class WriteBack:
    '''
    Wraps a writeback function which can be used to fill the lookup in case the required
    component descriptor was not found.
    '''
    def __init__(
        self,
        writeback: collections.abc.Callable[[ocm.ComponentIdentity, ocm.ComponentDescriptor], None],
    ):
        self.writeback = writeback

    def __call__(
        self,
        component_id: ocm.ComponentIdentity,
        component_descriptor: ocm.ComponentDescriptor,
    ):
        self.writeback(component_id, component_descriptor)


def in_memory_cache_component_descriptor_lookup(
    cache_ctor: cachetools.Cache=cachetools.LRUCache,
    ocm_repository_lookup: OcmRepositoryLookup=None,
    **cache_kwargs,
) -> ComponentDescriptorLookupById:
    '''
    Used to lookup referenced component descriptors in the in-memory cache.
    In case of a cache miss, the required component descriptor can be added
    to the cache by using the writeback function.

    @param cache_ctor:
        specification of the cache implementation
    @param ocm_repository_lookup:
        lookup for OCM repositories
    @param cache_kwargs:
        further args used for cache initialization, maxsize is defaulted to 2048
    '''
    cache_kwargs['maxsize'] = cache_kwargs.get('maxsize', 2048)
    cache = cache_ctor(**cache_kwargs)

    def writeback(
        component_id: ocm.ComponentIdentity,
        component_descriptor: ocm.ComponentDescriptor,
    ):
        if (ocm_repo := component_descriptor.component.current_ocm_repo):
            cache.__setitem__((component_id, ocm_repo), component_descriptor)
        else:
            raise ValueError(ocm_repo)

    _writeback = WriteBack(writeback)

    def lookup(
        component_id: ocm.ComponentIdentity,
        ocm_repository_lookup=ocm_repository_lookup,
    ):
        ocm_repos = iter_ocm_repositories(
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
    ocm_repository_lookup: OcmRepositoryLookup=None,
    cache_dir: str=None,
) -> ComponentDescriptorLookupById:
    '''
    Used to lookup referenced component descriptors in the file-system cache.
    In case of a cache miss, the required component descriptor can be added
    to the cache by using the writeback function. If cache_dir is not specified,
    it is tried to retrieve it from configuration (see `ctx`).

    @param ocm_repository_lookup:
        lookup for OCM repositories
    @param cache_dir:
        directory used for caching. Must exist, otherwise a ValueError is raised
    '''
    if not cache_dir:
        raise ValueError(cache_dir)

    def writeback(
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

    def lookup(
        component_id: cnudie.util.ComponentId,
        ocm_repository_lookup: OcmRepositoryLookup=ocm_repository_lookup,
    ):
        ocm_repos = iter_ocm_repositories(
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

            component_id = cnudie.util.to_component_id(component_id)

            descriptor_path = os.path.join(
                cache_dir,
                ocm_repo.oci_ref.replace('/', '-'),
                f'{component_id.name}-{component_id.version}',
            )
            if os.path.isfile(descriptor_path):
                return ocm.ComponentDescriptor.from_dict(
                    ci.util.parse_yaml_file(descriptor_path)
                )

        # component descriptor not found in lookup
        return _writeback

    return lookup


def delivery_service_component_descriptor_lookup(
    ocm_repository_lookup: OcmRepositoryLookup,
    delivery_client,
    default_absent_ok: bool=True,
    default_ignore_errors: tuple[Exception]=(
        requests.exceptions.ConnectionError,
        requests.exceptions.ReadTimeout,
    ),
    fallback_to_service_mapping: bool=True,
) -> ComponentDescriptorLookupById:
    '''
    Used to lookup referenced component descriptors in the delivery-service.

    @param ocm_repository_lookup:
        lookup for OCM repositories
    @param delivery_client:
        client to establish the connection to the delivery-service. If the client cannot be created,
        a ValueError is raised
    @param default_absent_ok:
        sets the default behaviour in case of absent component descriptors for the returned lookup
        function
    @param default_ignore_errors:
        collection of exceptions which should be ignored by default. In case of such an exception,
        no component descriptor will be returned, so that a subsequent lookup can retry retrieving
        it
    @param fallback_to_service_mapping:
        if set, it is tried to retrieve the requested component descriptor using the OCM repository
        mapping of the  delivery-service, in case it could not be retrieved using
        `ocm_repository_lookup`
    '''
    if not delivery_client:
        raise ValueError(delivery_client)

    def lookup(
        component_id: ocm.ComponentIdentity,
        ocm_repository_lookup: OcmRepositoryLookup=ocm_repository_lookup,
        absent_ok: bool=default_absent_ok,
        ignore_errors: tuple[Exception]=default_ignore_errors,
    ):
        component_id = cnudie.util.to_component_id(component_id)
        ocm_repos = iter_ocm_repositories(
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


def _raw_component_descriptor_from_oci(
    component_id: ocm.ComponentIdentity,
    ocm_repos: collections.abc.Iterable[ocm.OciOcmRepository | str],
    oci_client: oc.Client,
    absent_ok: bool=False,
) -> bytes:
    for ocm_repo in ocm_repos:
        if isinstance(ocm_repo, str):
            ocm_repo = ocm.OciOcmRepository(
                type=ocm.OciAccess,
                baseUrl=ocm_repo,
            )

        if not isinstance(ocm_repo, ocm.OciOcmRepository):
            raise NotImplementedError(ocm_repo)

        target_ref = ci.util.urljoin(
            ocm_repo.oci_ref,
            'component-descriptors',
            f'{component_id.name.lower()}:{component_id.version}', # oci-spec allows only lowercase
        )

        manifest = oci_client.manifest(
            image_reference=target_ref,
            absent_ok=True,
        )

        if manifest:
            break
    else:
        manifest = None

    if not manifest and absent_ok:
        return None
    elif not manifest:
        raise om.OciImageNotFoundException

    try:
        cfg_dict = json.loads(
            oci_client.blob(
                image_reference=target_ref,
                digest=manifest.config.digest,
            ).text
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
        if not (layers_count := len(manifest.layers) == 1):
            logger.warning(f'XXX unexpected amount of {layers_count=}')

        layer_digest = manifest.layers[0].digest
        layer_mimetype = manifest.layers[0].mediaType

    if not layer_mimetype in ocm.oci.component_descriptor_mimetypes:
        logger.warning(f'{target_ref=} {layer_mimetype=} was unexpected')
        # XXX: check for non-tar-variant

    return oci_client.blob(
        image_reference=target_ref,
        digest=layer_digest,
        stream=False, # manifests are typically small - do not bother w/ streaming
    ).content


def oci_component_descriptor_lookup(
    ocm_repository_lookup: OcmRepositoryLookup,
    oci_client: oc.Client | collections.abc.Callable[[], oc.Client],
    default_absent_ok=True,
) -> ComponentDescriptorLookupById:
    '''
    Used to lookup referenced component descriptors in the oci-registry.

    @param ocm_repository_lookup:
        lookup for OCM repositories
    @param oci_client:
        client to establish the connection to the oci-registry. If the client cannot be created, a
        ValueError is raised
    @param default_absent_ok:
        sets the default behaviour in case of absent component descriptors for the returned lookup
        function
    '''
    if not oci_client:
        raise ValueError(oci_client)

    def lookup(
        component_id: ocm.ComponentIdentity,
        ocm_repository_lookup: OcmRepositoryLookup=ocm_repository_lookup,
        absent_ok=default_absent_ok,
    ):
        if not ocm_repository_lookup:
            raise ValueError('ocm_repository_lookup must be passed')

        component_id = cnudie.util.to_component_id(component_id)

        if isinstance(oci_client, collections.abc.Callable):
            local_oci_client = oci_client()
        else:
            local_oci_client = oci_client

        ocm_repos = iter_ocm_repositories(
            component_id,
            ocm_repository_lookup,
        )

        raw = _raw_component_descriptor_from_oci(
            component_id=component_id,
            ocm_repos=ocm_repos,
            oci_client=local_oci_client,
            absent_ok=absent_ok,
        )
        if not raw and absent_ok:
            return
        elif not raw and not absent_ok:
            raise om.OciImageNotFoundException(component_id)

        # wrap in fobj
        blob_fobj = io.BytesIO(raw)
        try:
            component_descriptor = ocm.oci.component_descriptor_from_tarfileobj(
                fileobj=blob_fobj,
            )
        except tarfile.ReadError as tre:
            tre.add_note(f'{component_id=}')
            raise tre

        return component_descriptor

    return lookup


def error_code_indicating_not_found(image_reference: str | om.OciImageReference) -> int:
    '''
    Since some oci registries don't comply with the open containers spec
    (https://github.com/opencontainers/distribution-spec/blob/main/spec.md#endpoints)
    regarding the error codes for failure upon listing tags, this function tries to
    guess the oci registry type based on the supplied `image_reference` and returns
    the expected error code.
    '''
    oci_registry_type = om.OciRegistryType.from_image_ref(image_reference=image_reference)

    if oci_registry_type == om.OciRegistryType.DOCKERHUB:
        return 401
    if oci_registry_type == om.OciRegistryType.GHCR:
        return 403

    return 404


def version_lookup(
    ocm_repository_lookup: OcmRepositoryLookup,
    oci_client: oc.Client,
    default_absent_ok: bool=True,
) -> VersionLookupByComponent:
    if not oci_client:
        raise ValueError(oci_client)

    def lookup(
        component_id: ComponentName,
        ocm_repository_lookup: OcmRepositoryLookup=ocm_repository_lookup,
        absent_ok: bool=default_absent_ok,
    ):
        component_name = cnudie.util.to_component_name(component_id)
        ocm_repos = iter_ocm_repositories(
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
                for version_tag in _component_versions(
                    component_name=component_name,
                    ocm_repo=ocm_repo,
                    oci_client=oci_client,
                ):
                    versions.add(version_tag)
            except requests.exceptions.HTTPError as e:
                if (error_code := e.response.status_code) == 404:
                    continue

                image_reference = ocm_repo.component_oci_ref(component_name)
                if error_code == error_code_indicating_not_found(image_reference=image_reference):
                    continue

                raise

        if not versions and not absent_ok:
            raise om.OciImageNotFoundException()

        return versions

    return lookup


def composite_component_descriptor_lookup(
    lookups: tuple[ComponentDescriptorLookupById, ...],
    ocm_repository_lookup: OcmRepositoryLookup | None=None,
    default_absent_ok=True,
) -> ComponentDescriptorLookupById:
    '''
    Used to combine multiple ComponentDescriptorLookupByIds. The single lookups are used in
    the order they are specified. If the required component descriptor is found, it is
    written back to the prior lookups (if they have a WriteBack defined).

    @param lookups:
        a tuple of ComponentDescriptorLookupByIds which should be combined
    @param ocm_repository_lookup:
        ocm_repository_lookup to be used if none is specified in the lookup function
    @param default_absent_ok:
        sets the default behaviour in case of absent component descriptors for the returned lookup
        function
    '''
    def lookup(
        component_id: ocm.ComponentIdentity,
        /,
        ocm_repository_lookup=ocm_repository_lookup,
        absent_ok=default_absent_ok,
    ):
        component_id = cnudie.util.to_component_id(component_id)
        writebacks = []
        for lookup in lookups:
            res = None
            try:
                res = lookup(
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
                for wb in writebacks: wb(component_id, res)
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
    ocm_repository_lookup: OcmRepositoryLookup=None,
    cache_dir: str | None=None,
    oci_client: oc.Client | collections.abc.Callable[[], oc.Client]=None,
    delivery_client=None,
    default_absent_ok: bool=False,
    fallback_to_service_mapping: bool=True,
) -> ComponentDescriptorLookupById:
    '''
    This is a convenience function combining commonly used/recommended lookups, using global
    configuration if available. It combines (in this order) an in-memory cache, file-system cache,
    delivery-service based, and oci-registry based lookup.

    @param ocm_repository_lookup:
        lookup for OCM repositories
    @param cache_dir:
        directory used for caching. If cache_dir is not specified, the filesystem cache lookup is
        not included in the returned lookup
    @param oci_client:
        client to establish the connection to the oci-registry. If the client cannot be created, a
        ValueError is raised
    @param delivery_client:
        client to establish the connection to the delivery-service. If the client cannot be created,
        the delivery-service based lookup is not included in the returned lookup
    @param default_absent_ok:
        sets the default behaviour in case of absent component descriptors for the returned lookup
        function
    @param fallback_to_service_mapping:
        if set, it is tried to retrieve the requested component descriptor using the OCM repository
        mapping of the delivery-service, in case it could not be retrieved using
        `ocm_repository_lookup`
    '''
    if not ocm_repository_lookup:
        import ctx
        ocm_repository_lookup = ctx.cfg.ctx.ocm_repository_lookup

    lookups = [
        in_memory_cache_component_descriptor_lookup(
            ocm_repository_lookup=ocm_repository_lookup,
        )
    ]
    if not cache_dir:
        try:
            import ctx
            if ctx.cfg:
                cache_dir = ctx.cfg.ctx.cache_dir
        except ImportError:
            # ctx-module is an optional dependency for local dev setups
            pass

    if cache_dir:
        lookups.append(
            file_system_cache_component_descriptor_lookup(
                cache_dir=cache_dir,
                ocm_repository_lookup=ocm_repository_lookup,
            )
        )

    if delivery_client:
        lookups.append(delivery_service_component_descriptor_lookup(
            delivery_client=delivery_client,
            ocm_repository_lookup=ocm_repository_lookup,
            fallback_to_service_mapping=fallback_to_service_mapping,
        ))

    lookups.append(
        oci_component_descriptor_lookup(
            ocm_repository_lookup=ocm_repository_lookup,
            oci_client=oci_client,
        ),
    )

    return composite_component_descriptor_lookup(
        lookups=tuple(lookups),
        ocm_repository_lookup=ocm_repository_lookup,
        default_absent_ok=default_absent_ok,
    )


def component_diff(
    left_component: ocm.Component | ocm.ComponentDescriptor,
    right_component: ocm.Component | ocm.ComponentDescriptor,
    ignore_component_names=(),
    component_descriptor_lookup: ComponentDescriptorLookupById=None,
) -> cnudie.util.ComponentDiff:
    import cnudie.iter as ci # late import to avoid cyclic dependencies

    left_component = cnudie.util.to_component(left_component)
    right_component = cnudie.util.to_component(right_component)

    if not component_descriptor_lookup:
        component_descriptor_lookup = create_default_component_descriptor_lookup()

    left_components = tuple(
        component_node.component for component_node in ci.iter(
            component=left_component,
            lookup=component_descriptor_lookup,
            node_filter=ci.Filter.components,
        ) if component_node.component.name not in ignore_component_names
    )
    right_components = tuple(
        component_node.component for component_node in ci.iter(
            component=right_component,
            lookup=component_descriptor_lookup,
            node_filter=ci.Filter.components,
        ) if component_node.component.name not in ignore_component_names
    )

    return cnudie.util.diff_components(
        left_components=left_components,
        right_components=right_components,
        ignore_component_names=ignore_component_names,
    )


def _component_versions(
    component_name: str,
    ocm_repo: ocm.OcmRepository,
    oci_client: oc.Client,
) -> collections.abc.Sequence[str]:
    if not isinstance(ocm_repo, ocm.OciOcmRepository):
        raise ValueError(ocm_repo)

    ocm_repo: ocm.OciOcmRepository
    oci_ref = ocm_repo.component_oci_ref(component_name)

    return oci_client.tags(image_reference=oci_ref)
