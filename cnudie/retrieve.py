import dataclasses
import io
import json
import logging
import os
import shutil
import tempfile
import typing
import yaml

import cachetools
import dacite
import gci.componentmodel as cm
import gci.oci

import ccc.delivery
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

ComponentDescriptorLookupById = typing.Callable[
    [cm.ComponentIdentity, cm.RepositoryContext],
    cm.ComponentDescriptor
]


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
    default_ctx_repo: cm.RepositoryContext=None,
    cache_ctor: cachetools.Cache=cachetools.LRUCache,
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
        ctx_repo: cm.RepositoryContext=default_ctx_repo,
    ):
        if not ctx_repo:
            raise ValueError(ctx_repo)

        if not isinstance(ctx_repo, cm.OciRepositoryContext):
            raise NotImplementedError(ctx_repo)

        try:
            if (component_descriptor := cache.get((component_id, ctx_repo))):
                return component_descriptor
        except KeyError:
            pass

        # component descriptor not found in lookup
        return _writeback

    return lookup


def file_system_cache_component_descriptor_lookup(
    default_ctx_repo: cm.RepositoryContext=None,
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
                ctx_repo.baseUrl.replace('/', '-'),
                f'{component_id.name}-{component_id.version}',
            )
            shutil.move(f.name, descriptor_path)
        except:
            os.unlink(f.name)
            raise

    _writeback = WriteBack(writeback)

    def lookup(
        component_id: cm.ComponentIdentity,
        ctx_repo: cm.RepositoryContext=default_ctx_repo,
    ):
        if not ctx_repo:
            raise ValueError(ctx_repo)

        if not isinstance(ctx_repo, cm.OciRepositoryContext):
            raise NotImplementedError(ctx_repo)

        descriptor_path = os.path.join(
            cache_dir,
            ctx_repo.baseUrl.replace('/', '-'),
            f'{component_id.name}-{component_id.version}',
        )
        if os.path.isfile(descriptor_path):
            return cm.ComponentDescriptor.from_dict(
                ci.util.parse_yaml_file(descriptor_path)
            )
        else:
            base_dir = os.path.dirname(descriptor_path)
            os.makedirs(name=base_dir, exist_ok=True)

        # component descriptor not found in lookup
        return _writeback

    return lookup


def delivery_service_component_descriptor_lookup(
    default_ctx_repo: cm.RepositoryContext=None,
    delivery_client=None,
    default_absent_ok=True,
) -> ComponentDescriptorLookupById:
    '''
    Used to lookup referenced component descriptors in the delivery-service.

    @param default_ctx_repo: ctx_repo to be used if none is specified in the lookup function
    @param delivery_client:  client to establish the connection to the delivery-service. If \
                             the client cannot be created, a ValueError is raised
    '''
    if not delivery_client:
        delivery_client = ccc.delivery.default_client_if_available()
    if not delivery_client:
        raise ValueError(delivery_client)

    def lookup(
        component_id: cm.ComponentIdentity,
        ctx_repo: cm.RepositoryContext=default_ctx_repo,
        absent_ok=default_absent_ok,
    ):
        if not ctx_repo:
            raise ValueError(ctx_repo)

        if not isinstance(ctx_repo, cm.OciRepositoryContext):
            raise NotImplementedError(ctx_repo)

        try:
            return delivery_client.component_descriptor(
                name=component_id.name,
                version=component_id.version,
                ctx_repo_url=ctx_repo.baseUrl,
            )
        except:
            logger.info(f'{component_id.name}:{component_id.version} not found in delivery-svc')

        # component descriptor not found in lookup
        if absent_ok:
            return None
        raise om.OciImageNotFoundException

    return lookup


def oci_component_descriptor_lookup(
    default_ctx_repo: cm.RepositoryContext=None,
    oci_client: oc.Client=None,
    default_absent_ok=True,
) -> ComponentDescriptorLookupById:
    '''
    Used to lookup referenced component descriptors in the oci-registry.

    @param default_ctx_repo: ctx_repo to be used if none is specified in the lookup function
    @param oci_client:       client to establish the connection to the oci-registry. If the \
                             client cannot be created, a ValueError is raised
    '''
    if not oci_client:
        oci_client = ccc.oci.oci_client()
    if not oci_client:
        raise ValueError(oci_client)

    def lookup(
        component_id: cm.ComponentIdentity,
        ctx_repo: cm.RepositoryContext=default_ctx_repo,
        absent_ok=default_absent_ok,
    ):
        if not ctx_repo:
            raise ValueError(ctx_repo)

        if not isinstance(ctx_repo, cm.OciRepositoryContext):
            raise NotImplementedError(ctx_repo)

        component_name = component_id.name.lower() # oci-spec allows only lowercase

        target_ref = ci.util.urljoin(
            ctx_repo.baseUrl,
            'component-descriptors',
            f'{component_name}:{component_id.version}',
        )

        manifest = oci_client.manifest(
            image_reference=target_ref,
            absent_ok=True,
        )

        # check if component descriptor not found in lookup
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

        blob_res = oci_client.blob(
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


def composite_component_descriptor_lookup(
    lookups: typing.Tuple[ComponentDescriptorLookupById, ...],
    default_ctx_repo: cm.RepositoryContext=None,
    default_absent_ok=True,
) -> ComponentDescriptorLookupById:
    '''
    Used to combine multiple ComponentDescriptorLookupByIds. The single lookups are used in
    the order they are specified. If the required component descriptor is found, it is
    written back to the prior lookups (if they have a WriteBack defined).

    @param lookups:          a tuple of ComponentDescriptorLookupByIds which should be combined
    @param default_ctx_repo: ctx_repo to be used if none is specified in the lookup function
    '''
    def lookup(
        component_id: cm.ComponentIdentity,
        ctx_repo: cm.RepositoryContext=default_ctx_repo,
        absent_ok=default_absent_ok,
    ):
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
        raise om.OciImageNotFoundException

    return lookup


def create_default_component_descriptor_lookup(
    default_ctx_repo: cm.RepositoryContext=None,
    cache_dir: str=_cache_dir,
    delivery_client=None,
    default_absent_ok=False,
) -> ComponentDescriptorLookupById:
    '''
    This is a convenience function combining commonly used/recommended lookups, using global
    configuration if available. It combines (in this order) an in-memory cache, file-system cache,
    delivery-service based, and oci-registry based lookup.

    @param default_ctx_repo: ctx_repo to be used if none is specified in the lookup function
    @param cache_dir:        directory used for caching. If cache_dir does not exist, the file-\
                             system cache lookup is not included in the returned lookup
    @param delivery_client:  client to establish the connection to the delivery-service. If the \
                             client cannot be created, the delivery-service based lookup is not \
                             included in the returned lookup
    '''
    lookups = [in_memory_cache_component_descriptor_lookup()]
    if not cache_dir:
        if ctx and ctx.cfg:
            cache_dir = ctx.cfg.ctx.cache_dir

    if cache_dir:
        lookups.append(file_system_cache_component_descriptor_lookup(
            cache_dir=cache_dir,
        ))

    if not delivery_client:
        delivery_client = ccc.delivery.default_client_if_available()
    if delivery_client:
        lookups.append(delivery_service_component_descriptor_lookup(
            delivery_client=delivery_client,
        ))

    lookups.append(oci_component_descriptor_lookup())

    return composite_component_descriptor_lookup(
        lookups=tuple(lookups),
        default_ctx_repo=default_ctx_repo,
        default_absent_ok=default_absent_ok,
    )


def components(
    component: typing.Union[cm.ComponentDescriptor, cm.Component],
    component_descriptor_lookup: ComponentDescriptorLookupById=None,
):
    component = cnudie.util.to_component(component)

    if not component_descriptor_lookup:
        component_descriptor_lookup = create_default_component_descriptor_lookup(
            default_ctx_repo=component.current_repository_ctx(),
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
                component_id=cm.ComponentIdentity(
                    name=component_ref.componentName,
                    version=component_ref.version,
                ),
                ctx_repo=component.current_repository_ctx(),
            )

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
):
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
    ctx_repo: cm.RepositoryContext,
    oci_client: oc.Client=None,
) -> typing.Sequence[str]:
    if not isinstance(ctx_repo, cm.OciRepositoryContext):
        raise NotImplementedError(ctx_repo)

    if not oci_client:
        oci_client = ccc.oci.oci_client()

    ctx_repo: cm.OciRepositoryContext
    oci_ref = ctx_repo.component_oci_ref(component_name)

    return oci_client.tags(image_reference=oci_ref)


def greatest_component_version(
    component_name: str,
    ctx_repo: cm.RepositoryContext,
    oci_client: oc.Client=None,
    ignore_prerelease_versions: bool=False,
) -> str:
    if not isinstance(ctx_repo, cm.OciRepositoryContext):
        raise NotImplementedError(ctx_repo)

    if not oci_client:
        oci_client = ccc.oci.oci_client()

    image_tags = component_versions(
        component_name=component_name,
        ctx_repo=ctx_repo,
        oci_client=oci_client,
    )
    return version.find_latest_version(image_tags, ignore_prerelease_versions)


def greatest_component_versions(
    component_name: str,
    ctx_repo: cm.RepositoryContext,
    max_versions: int = 5,
    greatest_version: str = None,
    oci_client: oc.Client=None,
) -> list[str]:
    if not isinstance(ctx_repo, cm.OciRepositoryContext):
        raise NotImplementedError(ctx_repo)

    if not oci_client:
        oci_client = ccc.oci.oci_client()

    versions = component_versions(
        component_name=component_name,
        ctx_repo=ctx_repo,
        oci_client=oci_client,
    )

    if not versions:
        return []

    versions = sorted(versions, key=version.parse_to_semver)

    if greatest_version:
        versions = versions[:versions.index(greatest_version)+1]

    return versions[-max_versions:]


def greatest_version_before(
    component_name: str,
    component_version: str,
    ctx_repo: cm.RepositoryContext,
    oci_client: oc.Client=None,
) -> str | None:
    if not isinstance(ctx_repo, cm.OciRepositoryContext):
        raise NotImplementedError(ctx_repo)

    if not oci_client:
        oci_client = ccc.oci.oci_client()

    versions = component_versions(
        component_name=component_name,
        ctx_repo=ctx_repo,
        oci_client=oci_client,
    )
    versions = sorted(versions, key=version.parse_to_semver)
    versions = [
        v for v in versions
        if version.parse_to_semver(v) < version.parse_to_semver(component_version)
    ]
    if not versions:
        return None # no release before current was found
    return versions[-1]


def greatest_component_version_with_matching_minor(
    component_name: str,
    ctx_repo: cm.RepositoryContext,
    reference_version: str,
    ignore_prerelease_versions: bool=False,
    oci_client: oc.Client=None,
) -> str:
    if not isinstance(ctx_repo, cm.OciRepositoryContext):
        raise NotImplementedError(ctx_repo)

    if not oci_client:
        oci_client = ccc.oci.oci_client()

    versions = component_versions(
        component_name=component_name,
        ctx_repo=ctx_repo,
        oci_client=oci_client,
    )

    return version.find_latest_version_with_matching_minor(
        reference_version=reference_version,
        versions=versions,
        ignore_prerelease_versions=ignore_prerelease_versions,
    )


def greatest_component_version_by_name(
    component_name: str,
    ctx_repo: cm.RepositoryContext,
    ignore_prerelease_versions: bool=False,
    oci_client: oc.Client=None,
    component_descriptor_lookup: ComponentDescriptorLookupById=None,
) -> cm.Component:
    if not isinstance(ctx_repo, cm.OciRepositoryContext):
        raise NotImplementedError(ctx_repo)

    if not oci_client:
        oci_client = ccc.oci.oci_client()

    if not component_descriptor_lookup:
        component_descriptor_lookup = create_default_component_descriptor_lookup()

    greatest_version = greatest_component_version(
        component_name=component_name,
        ctx_repo=ctx_repo,
        oci_client=oci_client,
        ignore_prerelease_versions=ignore_prerelease_versions,
    )
    component_descriptor = component_descriptor_lookup(
        component_id=cm.ComponentIdentity(
            name=component_name,
            version=greatest_version,
        ),
        ctx_repo=ctx_repo,
    )
    return component_descriptor.component
