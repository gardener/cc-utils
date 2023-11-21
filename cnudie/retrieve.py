import dataclasses
import io
import json
import logging
import os
import shutil
import tempfile
import typing

import deprecated
import requests
import yaml

import cachetools
import dacite
import gci.componentmodel as cm
import gci.oci

import ccc.oci
import ci.util
import cnudie.util
import ctx
import oci.client as oc
import oci.model as om
import version


logger = logging.getLogger(__name__)
_cfg = ctx.cfg
_cache_dir = _cfg.ctx.component_descriptor_cache_dir

ComponentName = str | tuple[str, str] | cm.Component | cm.ComponentIdentity

ComponentDescriptorLookupById = typing.Callable[
    [cm.ComponentIdentity, cm.OcmRepository],
    cm.ComponentDescriptor
]

VersionLookupByComponent = typing.Callable[
    [ComponentName, cm.OcmRepository],
    typing.Sequence[str]
]

OcmRepositoryCfg = \
    str | typing.Iterable[str] | \
    cnudie.util.OcmLookupMappingConfig | \
    cnudie.util.OcmResolverConfig | \
    typing.Iterable[cnudie.util.OcmResolverConfig]


OcmRepositoryLookup = typing.Callable[
    [ComponentName],
    typing.Generator[cm.OciOcmRepository | str, None, None],
]


def _iter_ocm_repositories(
    component: str | cm.ComponentIdentity | cm.Component,
    repository_cfg: OcmRepositoryCfg,
    /,
):
    if isinstance(component, cm.ComponentIdentity):
        component = component.name
    elif isinstance(component, cm.Component):
        component = component.name

    if isinstance(repository_cfg, cm.OciOcmRepository):
        yield repository_cfg
        return

    if isinstance(repository_cfg, str):
        yield repository_cfg
        return

    if isinstance(repository_cfg, cnudie.util.OcmResolverConfig):
        repository_cfg: cnudie.util.OcmResolverConfig
        if repository_cfg.matches(component):
            yield repository_cfg.repository
        return

    if isinstance(repository_cfg, cnudie.util.OcmLookupMappingConfig):
        yield from repository_cfg.iter_ocm_repositories(component)

    # recurse into elements in case repository_cfg is iterable
    if hasattr(repository_cfg, '__iter__'):
        for cfg in repository_cfg:
            yield _iter_ocm_repositories(component, cfg)
        return


def iter_ocm_repositories(
    component: str | cm.ComponentIdentity | cm.Component,
    /,
    *repository_cfgs: OcmRepositoryCfg,
) -> typing.Generator[OcmRepositoryLookup, None, None]:
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
        component: str | cm.ComponentIdentity | cm.Component,
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
        writeback: typing.Callable[[cm.ComponentIdentity, cm.ComponentDescriptor], None],
    ):
        self.writeback = writeback

    def __call__(
        self,
        component_id: cm.ComponentIdentity,
        component_descriptor: cm.ComponentDescriptor,
    ):
        self.writeback(component_id, component_descriptor)


def in_memory_cache_component_descriptor_lookup(
    default_ctx_repo: cm.OcmRepository=None,
    cache_ctor: cachetools.Cache=cachetools.LRUCache,
    ocm_repository_lookup: OcmRepositoryLookup=None,
    **cache_kwargs,
) -> ComponentDescriptorLookupById:
    '''
    Used to lookup referenced component descriptors in the in-memory cache.
    In case of a cache miss, the required component descriptor can be added
    to the cache by using the writeback function.

    @param default_ctx_repo: ctx_repo to be used if none is specified in the lookup function
    @param cache_ctor:       specification of the cache implementation
    @param cache_kwargs:     further args used for cache initialization, maxsize is defaulted to 2048
    '''
    cache_kwargs['maxsize'] = cache_kwargs.get('maxsize', 2048)
    cache = cache_ctor(**cache_kwargs)

    def writeback(
        component_id: cm.ComponentIdentity,
        component_descriptor: cm.ComponentDescriptor,
    ):
        if (ctx_repo := component_descriptor.component.current_repository_ctx()):
            cache.__setitem__((component_id, ctx_repo), component_descriptor)
        else:
            raise ValueError(ctx_repo)

    _writeback = WriteBack(writeback)

    def lookup(
        component_id: cm.ComponentIdentity,
        ctx_repo: cm.OcmRepository=default_ctx_repo,
        ocm_repository_lookup=ocm_repository_lookup,
    ):
        if ctx_repo:
            ocm_repos = (ctx_repo,)

        else:
            ocm_repos = iter_ocm_repositories(
                component_id,
                ocm_repository_lookup,
                default_ctx_repo,
            )

        for ocm_repo in ocm_repos:
            if isinstance(ocm_repo, str):
                ocm_repo = cm.OciOcmRepository(
                    type=cm.AccessType.OCI_REGISTRY,
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
    default_ctx_repo: cm.OcmRepository=None,
    ocm_repository_lookup: OcmRepositoryLookup=None,
    cache_dir: str=_cache_dir,
) -> ComponentDescriptorLookupById:
    '''
    Used to lookup referenced component descriptors in the file-system cache.
    In case of a cache miss, the required component descriptor can be added
    to the cache by using the writeback function. If cache_dir is not specified,
    it is tried to retrieve it from configuration (see `ctx`).

    @param default_ctx_repo: ctx_repo to be used if none is specified in the lookup function
    @param cache_dir:        directory used for caching. Must exist, other a ValueError is raised
    '''
    if not cache_dir:
        raise ValueError(cache_dir)

    def writeback(
        component_id: cm.ComponentIdentity,
        component_descriptor: cm.ComponentDescriptor,
    ):
        if not (ctx_repo := component_descriptor.component.current_repository_ctx()):
            raise ValueError(ctx_repo)

        try:
            f = tempfile.NamedTemporaryFile(mode='w', delete=False)
            # write to tempfile, followed by a mv to avoid collisions through concurrent
            # processes or threads (assuming mv is an atomic operation)
            yaml.dump(
                data=dataclasses.asdict(component_descriptor),
                Dumper=cm.EnumValueYamlDumper,
                stream=f.file,
            )
            f.close() # need to close filehandle for NT

            descriptor_path = os.path.join(
                cache_dir,
                ctx_repo.oci_ref.replace('/', '-'),
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
        ctx_repo: cm.OcmRepository|str=default_ctx_repo,
        ocm_repository_lookup: OcmRepositoryLookup=ocm_repository_lookup,
    ):
        if ctx_repo:
            ocm_repos = (ctx_repo, )
        else:
            ocm_repos = iter_ocm_repositories(
                component_id,
                ocm_repository_lookup,
                default_ctx_repo,
            )

        for ocm_repo in ocm_repos:
            if not ocm_repo:
                raise ValueError(ocm_repo)

            if isinstance(ocm_repo, str):
                ocm_repo = cm.OciOcmRepository(
                    type=cm.AccessType.OCI_REGISTRY,
                    baseUrl=ocm_repo,
                )

            if not isinstance(ocm_repo, cm.OciOcmRepository):
                raise NotImplementedError(ocm_repo)

            component_id = cnudie.util.to_component_id(component_id)

            descriptor_path = os.path.join(
                cache_dir,
                ocm_repo.oci_ref.replace('/', '-'),
                f'{component_id.name}-{component_id.version}',
            )
            if os.path.isfile(descriptor_path):
                return cm.ComponentDescriptor.from_dict(
                    ci.util.parse_yaml_file(descriptor_path)
                )

        # component descriptor not found in lookup
        return _writeback

    return lookup


def delivery_service_component_descriptor_lookup(
    default_ctx_repo: cm.OcmRepository=None,
    ocm_repository_lookup: OcmRepositoryLookup=None,
    delivery_client=None,
    default_absent_ok=True,
) -> ComponentDescriptorLookupById:
    '''
    Used to lookup referenced component descriptors in the delivery-service.

    @param default_ctx_repo:    ctx_repo to be used if none is specified in the lookup function
    @param delivery_client:     client to establish the connection to the delivery-service. If \
                                the client cannot be created, a ValueError is raised
    @param default_absent_ok:   sets the default behaviour in case of absent component \
                                descriptors for the returned lookup function
    '''
    if not delivery_client:
        import ccc.delivery
        delivery_client = ccc.delivery.default_client_if_available()
    if not delivery_client:
        raise ValueError(delivery_client)

    def lookup(
        component_id: cm.ComponentIdentity,
        ocm_repository_lookup: OcmRepositoryLookup=ocm_repository_lookup,
        ctx_repo: cm.OcmRepository=default_ctx_repo,
        absent_ok=default_absent_ok,
    ):
        component_id = cnudie.util.to_component_id(component_id)
        if ctx_repo:
            ocm_repos = (ctx_repo, )
        else:
            ocm_repos = iter_ocm_repositories(
                component_id,
                ocm_repository_lookup,
                default_ctx_repo,
            )

        for ocm_repo in ocm_repos:
            if isinstance(ocm_repo, str):
                ocm_repo = cm.OciOcmRepository(
                    type=cm.AccessType.OCI_REGISTRY,
                    baseUrl=ocm_repo,
                )

            if not isinstance(ocm_repo, cm.OciOcmRepository):
                raise NotImplementedError(ocm_repo)

            try:
                return delivery_client.component_descriptor(
                    name=component_id.name,
                    version=component_id.version,
                    ctx_repo_url=ocm_repo.oci_ref,
                )
            except requests.exceptions.HTTPError:
                # XXX: might want to warn about errors other than http-404
                pass

        # component descriptor not found in lookup
        if absent_ok:
            return None
        raise om.OciImageNotFoundException

    return lookup


def oci_component_descriptor_lookup(
    default_ctx_repo: cm.OcmRepository=None,
    ocm_repository_lookup: OcmRepositoryLookup=None,
    oci_client: oc.Client | typing.Callable[[], oc.Client]=None,
    default_absent_ok=True,
) -> ComponentDescriptorLookupById:
    '''
    Used to lookup referenced component descriptors in the oci-registry.

    @param default_ctx_repo:    ctx_repo to be used if none is specified in the lookup function
    @param oci_client:          client to establish the connection to the oci-registry. If the \
                                client cannot be created, a ValueError is raised
    @param default_absent_ok:   sets the default behaviour in case of absent component \
                                descriptors for the returned lookup function
    '''
    if not oci_client:
        oci_client = ccc.oci.oci_client()
    if not oci_client:
        raise ValueError(oci_client)

    def lookup(
        component_id: cm.ComponentIdentity,
        ctx_repo: cm.OcmRepository=default_ctx_repo,
        ocm_repository_lookup: OcmRepositoryLookup=ocm_repository_lookup,
        absent_ok=default_absent_ok,
    ):
        component_id = cnudie.util.to_component_id(component_id)
        component_name = component_id.name.lower() # oci-spec allows only lowercase

        if isinstance(oci_client, typing.Callable):
            local_oci_client = oci_client()
        else:
            local_oci_client = oci_client

        if ctx_repo:
            ocm_repos = (ctx_repo,)
        else:
            if not ocm_repository_lookup:
                raise ValueError('either ctx_repo, or ocm_repository_lookup must be passed')

            ocm_repos = iter_ocm_repositories(
                component_id,
                ocm_repository_lookup,
                default_ctx_repo,
            )

        for ocm_repo in ocm_repos:
            if isinstance(ocm_repo, str):
                ocm_repo = cm.OciOcmRepository(
                    type=cm.OciAccess,
                    baseUrl=ocm_repo,
                )

            if not isinstance(ocm_repo, cm.OciOcmRepository):
                raise NotImplementedError(ocm_repo)

            target_ref = ci.util.urljoin(
                ocm_repo.oci_ref,
                'component-descriptors',
                f'{component_name}:{component_id.version}',
            )

            manifest = local_oci_client.manifest(
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
                local_oci_client.blob(
                    image_reference=target_ref,
                    digest=manifest.config.digest,
                ).text
            )
            cfg = dacite.from_dict(
                data_class=gci.oci.ComponentDescriptorOciCfg,
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

        if not layer_mimetype in gci.oci.component_descriptor_mimetypes:
            logger.warning(f'{target_ref=} {layer_mimetype=} was unexpected')
            # XXX: check for non-tar-variant

        blob_res = local_oci_client.blob(
            image_reference=target_ref,
            digest=layer_digest,
            stream=False, # manifests are typically small - do not bother w/ streaming
        )
        # wrap in fobj
        blob_fobj = io.BytesIO(blob_res.content)
        component_descriptor = gci.oci.component_descriptor_from_tarfileobj(
            fileobj=blob_fobj,
        )

        return component_descriptor

    return lookup


def version_lookup(
    default_ctx_repo: cm.OcmRepository=None,
    ocm_repository_lookup: OcmRepositoryLookup=None,
    oci_client: oc.Client=None,
    default_absent_ok=True,
) -> VersionLookupByComponent:
    if not oci_client:
        oci_client = ccc.oci.oci_client()
    if not oci_client:
        raise ValueError(oci_client)

    def lookup(
        component_id: ComponentName,
        ctx_repo: cm.OcmRepository=default_ctx_repo,
        ocm_repository_lookup: OcmRepositoryLookup=ocm_repository_lookup,
        absent_ok: bool=default_absent_ok,
    ):
        component_name = cnudie.util.to_component_name(component_id)
        if ctx_repo:
            ocm_repos = (ctx_repo, )
        else:
            ocm_repos = iter_ocm_repositories(
                component_name,
                ocm_repository_lookup,
                default_ctx_repo,
            )

        versions = set()
        for ocm_repo in ocm_repos:
            if isinstance(ocm_repo, str):
                ocm_repo = cm.OciOcmRepository(
                    type=cm.OciAccess,
                    baseUrl=ocm_repo,
                )
            if not isinstance(ocm_repo, cm.OciOcmRepository):
                raise NotImplementedError(ocm_repo)

            for version_tag in component_versions(
                component_name=component_name,
                ctx_repo=ocm_repo,
                oci_client=oci_client,
            ):
                versions.add(version_tag)

        if not versions and not absent_ok:
            raise om.OciImageNotFoundException()

        return versions

    return lookup


def composite_component_descriptor_lookup(
    lookups: typing.Tuple[ComponentDescriptorLookupById, ...],
    ocm_repository_lookup: OcmRepositoryLookup | None=None,
    default_absent_ok=True,
) -> ComponentDescriptorLookupById:
    '''
    Used to combine multiple ComponentDescriptorLookupByIds. The single lookups are used in
    the order they are specified. If the required component descriptor is found, it is
    written back to the prior lookups (if they have a WriteBack defined).

    @param lookups:          a tuple of ComponentDescriptorLookupByIds which should be combined
    @param ocm_repository_lookup: ocm_repository_lookup to be used if none is specified
                                  in the lookup function
    '''
    def lookup(
        component_id: cm.ComponentIdentity,
        /,
        ctx_repo: cm.OciOcmRepository|str=None,
        ocm_repository_lookup=ocm_repository_lookup,
        absent_ok=default_absent_ok,
    ):
        component_id = cnudie.util.to_component_id(component_id)
        writebacks = []
        for lookup in lookups:
            res = None
            try:
                if ctx_repo:
                    res = lookup(component_id, ctx_repo)
                else:
                    res = lookup(component_id)
            except om.OciImageNotFoundException:
                pass

            if isinstance(res, cm.ComponentDescriptor):
                for wb in writebacks: wb(component_id, res)
                return res
            elif res is None: continue
            elif isinstance(res, WriteBack): writebacks.append(res)

        # component descriptor not found in lookup
        if absent_ok:
            return

        if isinstance(ctx_repo, str):
            ctx_repo = cm.OciOcmRepository(
                type=cm.OciAccess,
                baseUrl=ctx_repo,
            )

        if ctx_repo:
            component_url = ctx_repo.component_version_oci_ref(component_id)
        elif ocm_repository_lookup:
            def to_repo_url(ocm_repo):
                if isinstance(ocm_repo, str):
                    return ocm_repo
                else:
                    return ocm_repo.oci_ref

            ocm_repository_urls = '\n'.join(
                to_repo_url(ocm_repository) for ocm_repository
                in ocm_repository_lookup(component_id)
            )
            component_url = f'ocm-repositories:\n{ocm_repository_urls}:\n{str(component_id)}'
        else:
            component_url = f'<no ocm-repo given>: {str(component_id)}'

        raise om.OciImageNotFoundException(
            component_url,
        )

    return lookup


def create_default_component_descriptor_lookup(
    default_ctx_repo: cm.OcmRepository=None,
    ocm_repository_lookup: OcmRepositoryLookup=None,
    cache_dir: str=_cache_dir,
    oci_client: oc.Client=None,
    delivery_client=None,
    default_absent_ok=False,
) -> ComponentDescriptorLookupById:
    '''
    This is a convenience function combining commonly used/recommended lookups, using global
    configuration if available. It combines (in this order) an in-memory cache, file-system cache,
    delivery-service based, and oci-registry based lookup.

    @param default_ctx_repo: deprecated. use ocm_repository_lookup instead
    @param ocm_repository_lookup: lookup for OCM Repositories
    @param cache_dir:        directory used for caching. If cache_dir does not exist, the file-\
                             system cache lookup is not included in the returned lookup
    @param delivery_client:  client to establish the connection to the delivery-service. If the \
                             client cannot be created, the delivery-service based lookup is not \
                             included in the returned lookup
    '''
    if not ocm_repository_lookup:
        ocm_repository_lookup = ctx.cfg.ctx.ocm_repository_lookup

    if ocm_repository_lookup and default_ctx_repo:
        raise ValueError('default_ctx_repo and ocm_repository_lookup must not both be passed')

    if default_ctx_repo:
        logger.warn('passing default_ctx_repo is deprecated')
        ocm_repository_lookup = globals()['ocm_repository_lookup'](
            default_ctx_repo,
        )

    lookups = [
        in_memory_cache_component_descriptor_lookup(
            ocm_repository_lookup=ocm_repository_lookup,
        )
    ]
    if not cache_dir:
        if ctx and ctx.cfg:
            cache_dir = ctx.cfg.ctx.cache_dir

    if cache_dir:
        lookups.append(
            file_system_cache_component_descriptor_lookup(
                cache_dir=cache_dir,
                ocm_repository_lookup=ocm_repository_lookup,
            )
        )

    if not delivery_client:
        import ccc.delivery
        delivery_client = ccc.delivery.default_client_if_available()
    if delivery_client:
        lookups.append(delivery_service_component_descriptor_lookup(
            delivery_client=delivery_client,
            ocm_repository_lookup=ocm_repository_lookup,
        ))

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


def components(
    component: typing.Union[cm.ComponentDescriptor, cm.Component],
    component_descriptor_lookup: ComponentDescriptorLookupById=None,
):
    component = cnudie.util.to_component(component)

    if not component_descriptor_lookup:
        component_descriptor_lookup = create_default_component_descriptor_lookup(
            ocm_repository_lookup=ocm_repository_lookup(
                component.current_repository_ctx(),
            ),
        )

    _visited_component_versions = [
        (component.name, component.version)
    ]

    def resolve_component_dependencies(
        component: cm.Component,
    ) -> typing.Generator[cm.Component, None, None]:
        nonlocal _visited_component_versions

        yield component

        for component_ref in component.componentReferences:
            cref = (component_ref.componentName, component_ref.version)

            if cref in _visited_component_versions:
                continue
            else:
                _visited_component_versions.append(cref)

            resolved_component_descriptor = component_descriptor_lookup(
                cm.ComponentIdentity(
                    name=component_ref.componentName,
                    version=component_ref.version,
                ),
            )

            if not resolved_component_descriptor:
                logger.error(f'failed to find {component_ref=}')
                raise RuntimeError(component_ref, component_descriptor_lookup)

            yield from resolve_component_dependencies(
                component=resolved_component_descriptor.component,
            )

    yield from resolve_component_dependencies(
        component=component,
    )


def component_diff(
    left_component: typing.Union[cm.Component, cm.ComponentDescriptor],
    right_component: typing.Union[cm.Component, cm.ComponentDescriptor],
    ignore_component_names=(),
    component_descriptor_lookup: ComponentDescriptorLookupById=None,
) -> cnudie.util.ComponentDiff:
    left_component = cnudie.util.to_component(left_component)
    right_component = cnudie.util.to_component(right_component)

    if not component_descriptor_lookup:
        component_descriptor_lookup = create_default_component_descriptor_lookup()

    left_components = tuple(
        c for c in
        components(
            component=left_component,
            component_descriptor_lookup=component_descriptor_lookup,
        )
        if c.name not in ignore_component_names
    )
    right_components = tuple(
        c for c in
        components(
            component=right_component,
            component_descriptor_lookup=component_descriptor_lookup,
        )
        if c.name not in ignore_component_names
    )

    return cnudie.util.diff_components(
        left_components=left_components,
        right_components=right_components,
        ignore_component_names=ignore_component_names,
    )


def component_versions(
    component_name: str,
    ctx_repo: cm.OcmRepository,
    oci_client: oc.Client=None,
) -> typing.Sequence[str]:
    if not isinstance(ctx_repo, cm.OciOcmRepository):
        raise NotImplementedError(ctx_repo)

    if not oci_client:
        oci_client = ccc.oci.oci_client()

    ctx_repo: cm.OciOcmRepository
    oci_ref = ctx_repo.component_oci_ref(component_name)

    return oci_client.tags(image_reference=oci_ref)


# moved to delivery-service
# TODO remove once all usages of this functions are updated
@deprecated.deprecated
def greatest_component_versions(
    component_name: str,
    ctx_repo: cm.OcmRepository=None,
    max_versions: int = 5,
    greatest_version: str = None,
    oci_client: oc.Client = None,
    ignore_prerelease_versions: bool = False,
    version_lookup: VersionLookupByComponent=None,
    invalid_semver_ok: bool=False,
) -> list[str]:
    if not ctx_repo and not version_lookup:
        raise ValueError('At least one of `ctx_repo` and `version_lookup` has to be specified')

    if ctx_repo:
        if not isinstance(ctx_repo, cm.OciOcmRepository):
            raise NotImplementedError(ctx_repo)

        if not oci_client:
            oci_client = ccc.oci.oci_client()

        versions = component_versions(
            component_name=component_name,
            ctx_repo=ctx_repo,
            oci_client=oci_client,
        )
    else:
        versions = version_lookup(
            cm.ComponentIdentity(
                name=component_name,
                version=None
            ),
        )

    if not versions:
        return []

    versions = [
        v
        for v in versions
        if version.parse_to_semver(
            version=v,
            invalid_semver_ok=invalid_semver_ok,
        )
    ]

    if ignore_prerelease_versions:
        versions = [
            v
            for v in versions
            if not (pv := version.parse_to_semver(v, invalid_semver_ok)).prerelease and not pv.build
        ]

    versions = sorted(versions, key=lambda v: version.parse_to_semver(v, invalid_semver_ok))

    if greatest_version:
        versions = versions[:versions.index(greatest_version)+1]

    return versions[-max_versions:]
