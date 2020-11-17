'''
utils used for transitioning to new component-descriptor v2

see: https://github.com/gardener/component-spec
'''

import dataclasses
import enum
import io
import itertools
import json
import os
import shutil
import tempfile
import typing
import yaml

import dacite

import gci.componentmodel as cm
import gci.oci

import ci.util
import container.registry
import product.model
import product.util
import version


COMPONENT_TYPE_NAME = 'component'


def convert_to_v1(
    component_descriptor_v2: cm.ComponentDescriptor,
):
    component_v2 = component_descriptor_v2.component
    component_descriptor_v1 = product.model.ComponentDescriptor.from_dict(raw_dict={})

    component_v1 = _convert_component_to_v1(component_v2=component_v2)
    component_descriptor_v1.add_component(component_v1)

    component_deps = component_v1.dependencies()

    for component_ref in component_v2.componentReferences:
        component_deps.add_component_dependency(
            product.model.ComponentReference.create(
                name=component_ref.componentName,
                version=component_ref.version,
            )
        )
    # todo: also resolve component references (delegate for now)
    return component_descriptor_v1


def _convert_component_to_v1(
    component_v2: cm.Component,
):
    component_v1 = product.model.Component.create(
        name=component_v2.name,
        version=component_v2.version
    )
    component_deps = component_v1.dependencies()

    for resource in component_v2.resources:
        if hasattr(resource, 'relation'):
            if resource.relation is cm.ResourceRelation.LOCAL:
                v1_relation = product.model.Relation.LOCAL
            elif resource.relation is cm.ResourceRelation.EXTERNAL:
                v1_relation = product.model.Relation.THIRD_PARTY
            else:
                raise NotImplementedError
        else:
            v1_relation = product.model.Relation.LOCAL

        if resource.type is cm.ResourceType.OCI_IMAGE:
            component_deps.add_container_image_dependency(
                product.model.ContainerImage.create(
                    name=resource.name,
                    version=resource.version,
                    image_reference=resource.access.imageReference,
                    relation=v1_relation,
                )
            )
        elif resource.type is cm.ResourceType.GENERIC:
            component_deps.add_generic_dependency(
                product.model.GenericDependency.create(
                    name=resource.name,
                    version=resource.version,
                )
            )

    return component_v1


def _normalise_component_name(component_name:str) -> str:
    return component_name.lower()  # oci-spec allows only lowercase


def _convert_dependencies_to_v2_resources(
    component_descriptor_v1: product.model.ComponentDescriptor,
    component_v1: product.model.Component,
):
    '''
    calculates effective dependencies (for OCI image dependencies, only), and yields
    their component-descriptor v2-equivalents. Filters by component-relation
    '''

    for container_image in product.util._effective_images(
        component_descriptor=component_descriptor_v1,
        component=component_v1,
    ):
        relation = product.model.Relation(container_image.relation())
        # translate to target (v2) model
        if relation is product.model.Relation.LOCAL:
            relation = cm.ResourceRelation.LOCAL
        elif relation is product.model.Relation.THIRD_PARTY:
            relation = cm.ResourceRelation.EXTERNAL

        yield cm.Resource(
            name=container_image.name(),
            version=container_image.version(),
            type=cm.ResourceType.OCI_IMAGE,
            relation=relation,
            access=cm.OciAccess(
                type=cm.AccessType.OCI_REGISTRY,
                imageReference=container_image.image_reference(),
            )
        )

    # the other dependency-types were so far never subject to overwrites, so no need
    # to calculate effective dependencies
    dependencies = component_v1.dependencies()

    # actually, only generic dependencies need to be considered in addition to OCI images,
    # as we dependencies were never used until today
    for generic_dependency in dependencies.generic_dependencies():
        if not generic_dependency.relation() is relation:
            continue

        yield cm.Resource(
            name=generic_dependency.name(),
            version=generic_dependency.version(),
            type=cm.ResourceType.GENERIC,
            access=cm.ResourceAccess(
                type=cm.AccessType.NONE,
            )
        )


def convert_component_to_v2(
    component_descriptor_v1: product.model.ComponentDescriptor,
    component_v1: product.model.Component,
    repository_ctx_base_url: str,
):
    '''
    converts the given component from the given component descriptor into the new (v2)
    component descriptor format.

    Note that different as done in v1, dependencies are not resolved. Overwrites are
    applied (but not incorporated into the resulting component descriptor v2)
    '''
    def mk_component_references():
        # component names must be unique - append version, if required
        for _, components in itertools.groupby(
            sorted(
                component_v1.dependencies().components(),
                key=lambda c: c.name(),
            ),
            key=lambda c: c.name(),
        ):
            components = list(components)
            if len(components) == 1:
                append_version = False
            else:
                append_version = True

            for component in components:
                if append_version:
                    name = f'{component.name()}-{component.version()}'
                else:
                    name = component.name()

                yield cm.ComponentReference(
                    name=name,
                    componentName=component.name(),
                    version=component.version(),
                )

    component_descriptor = cm.ComponentDescriptor(
        meta=cm.Metadata(
            schemaVersion=cm.SchemaVersion.V2,
        ),
        component=cm.Component(
            name=component_v1.name(),
            version=component_v1.version(),

            repositoryContexts=[
                cm.RepositoryContext(
                    baseUrl=repository_ctx_base_url,
                    type=cm.AccessType.OCI_REGISTRY,
                ),
            ],
            provider=cm.Provider.INTERNAL,

            sources=[
                cm.ComponentSource(
                    name=component_v1.name(),
                    type=cm.SourceType.GIT,
                    access=cm.GithubAccess(
                        type=cm.AccessType.GITHUB,
                        repoUrl=component_v1.name(),
                        ref=f'refs/tags/{component_v1.version()}',
                    )
                )
            ],
            componentReferences=[
                component_ref for component_ref in mk_component_references()
            ],
            resources=[
                resource for resource in
                _convert_dependencies_to_v2_resources(
                    component_descriptor_v1=component_descriptor_v1,
                    component_v1=component_v1,
                )
            ],
        ),
    )

    return component_descriptor


def ensure_is_v2(
    component_descriptor_v2: gci.componentmodel.ComponentDescriptor,
):
    schema_version = component_descriptor_v2.meta.schemaVersion
    if not schema_version is gci.componentmodel.SchemaVersion.V2:
        raise RuntimeError(f'unsupported component-descriptor-version: {schema_version=}')


def _target_oci_repository_from_component_name(component_name: str, ctx_repo_base_url: str):
    component_name = _normalise_component_name(component_name)
    return ci.util.urljoin(
        ctx_repo_base_url,
        'component-descriptors',
        f'{component_name}',
    )


def _target_oci_ref(
    component: gci.componentmodel.Component,
    component_ref: gci.componentmodel.ComponentReference=None,
    component_version: str=None,
):

    if not component_ref:
        component_ref = component
        component_name = component_ref.name
    else:
        component_name = component_ref.componentName

    component_name = _normalise_component_name(component_name)
    component_version = component_ref.version

    # last ctx-repo is target-repository
    last_ctx_repo = component.repositoryContexts[-1]
    base_url = last_ctx_repo.baseUrl

    repository = _target_oci_repository_from_component_name(
        component_name=component_name,
        ctx_repo_base_url=base_url,
    )

    return f'{repository}:{component_version}'


def _target_oci_ref_from_ctx_base_url(
    component_name: str,
    component_version: str,
    ctx_repo_base_url: str,
):
    component_name = _normalise_component_name(component_name)

    return ci.util.urljoin(
        ctx_repo_base_url,
        'component-descriptors',
        f'{component_name}:{component_version}',
    )


def download_component_descriptor_v2(
    component_name: str,
    component_version: str,
    ctx_repo_base_url: str,
    absent_ok: bool=False,
    cache_dir: str=None,
):
    target_ref = _target_oci_ref_from_ctx_base_url(
        component_name=component_name,
        component_version=component_version,
        ctx_repo_base_url=ctx_repo_base_url,
    )

    if cache_dir:
        descriptor_path = os.path.join(
            cache_dir,
            ctx_repo_base_url.replace('/', '-'),
            f'{component_name}-{component_version}',
        )
        if os.path.isfile(descriptor_path):
            return cm.ComponentDescriptor.from_dict(
                ci.util.parse_yaml_file(descriptor_path)
            )
        else:
            base_dir = os.path.dirname(descriptor_path)
            os.makedirs(name=base_dir, exist_ok=True)

    component_descriptor =  retrieve_component_descriptor_from_oci_ref(
        manifest_oci_image_ref=target_ref,
        absent_ok=absent_ok,
    )

    if absent_ok and not component_descriptor:
        return None

    if cache_dir:
        try:
            f = tempfile.NamedTemporaryFile(mode='w', delete=False)
            # write to tempfile, followed by a mv to avoid collisions through concurrent
            # processes or threads (assuming mv is an atomic operation)
            yaml.dump(
                data=dataclasses.asdict(component_descriptor),
                Dumper=cm.EnumValueYamlDumper,
                stream=f.file,
            )
            shutil.move(f.name, descriptor_path)
        except:
            os.unlink(f.name)
            raise

    return component_descriptor


class UploadMode(enum.Enum):
    SKIP = 'skip'
    FAIL = 'fail'
    OVERWRITE = 'overwrite'


def write_component_descriptor_to_dir(
    component_descriptor: gci.componentmodel.ComponentDescriptor,
    cache_dir: str,
    on_exist=UploadMode.SKIP,
    ctx_repo_base_url: str=None, # if none, use current from component-descriptor
):
    if not os.path.isdir(cache_dir):
        raise ValueError(f'not a directory: {cache_dir=}')

    if not ctx_repo_base_url:
        ctx_repo_base_url = component_descriptor.component.current_repository_ctx().baseUrl

    component = component_descriptor.component
    descriptor_path = os.path.join(
        cache_dir,
        ctx_repo_base_url.replace('/', '-'),
        f'{component.name}-{component.version}',
    )

    if os.path.isfile(descriptor_path):
        if on_exist is UploadMode.SKIP:
            return component_descriptor,
        elif on_exist is UploadMode.FAIL:
            raise ValueError(f'already exists: {descriptor_path=}, but overwrite not allowed')
        elif on_exist is OVERWRITE:
            pass
        else:
            raise NotImplementedError(on_exit)

    if not os.path.isdir((pdir := os.path.dirname(descriptor_path))):
        os.makedirs(pdir, exist_ok=True)

    try:
        f = tempfile.NamedTemporaryFile(mode='w', delete=False)
        # write to tempfile, followed by a mv to avoid collisions through concurrent
        # processes or threads (assuming mv is an atomic operation)
        yaml.dump(
            data=dataclasses.asdict(component_descriptor),
            Dumper=cm.EnumValueYamlDumper,
            stream=f.file,
        )
        shutil.move(f.name, descriptor_path)
    except:
        os.unlink(f.name)
        raise


def upload_component_descriptor_v2_to_oci_registry(
    component_descriptor_v2: gci.componentmodel.ComponentDescriptor,
    on_exist=UploadMode.SKIP,
):
    ensure_is_v2(component_descriptor_v2)

    target_ref = _target_oci_ref(component_descriptor_v2.component)

    if on_exist in (UploadMode.SKIP, UploadMode.FAIL):
        if container.registry._image_exists(image_reference=target_ref):
            if on_exist is UploadMode.SKIP:
                return
            if on_exist is UploadMode.FAIL:
                # XXX: we might still ignore it, if the to-be-uploaded CD is equal to the existing
                # one
                raise ValueError(f'{target_ref=} already existed')
    elif on_exist is UploadMode.OVERWRITE:
        pass
    else:
        raise NotImplementedError(on_exist)

    raw_fobj = gci.oci.component_descriptor_to_tarfileobj(component_descriptor_v2)

    # upload cd-blob
    cd_digest = container.registry.put_blob(
        target_ref,
        fileobj=raw_fobj,
        mimetype=gci.oci.component_descriptor_mimetype,
    )
    cd_digest_with_alg = f'sha256:{cd_digest}'

    cfg = gci.oci.ComponentDescriptorOciCfg(
        componentDescriptorLayer=gci.oci.ComponentDescriptorOciBlobRef(
            digest=cd_digest_with_alg,
            size=raw_fobj.tell(),
        )
    )
    cfg_raw = json.dumps(dataclasses.asdict(cfg)).encode('utf-8')
    cfg_fobj = io.BytesIO(cfg_raw)
    cfg_digest = container.registry.put_blob(
        target_ref,
        fileobj=cfg_fobj,
        mimetype=container.registry.docker_http.OCI_CONFIG_JSON_MIME,
    )

    manifest = container.registry.OciImageManifest(
        config=gci.oci.ComponentDescriptorOciCfgBlobRef(
            digest=f'sha256:{cfg_digest}',
            size=cfg_fobj.tell(),
        ),
        layers=[
            gci.oci.ComponentDescriptorOciBlobRef(
                digest=cd_digest_with_alg,
                size=raw_fobj.tell(),
            ),
        ],
    )

    container.registry.put_image_manifest(
        image_reference=target_ref,
        manifest=manifest,
    )


def retrieve_component_descriptor_from_oci_ref(
    manifest_oci_image_ref: str,
    absent_ok=False,
):
    manifest = container.registry.retrieve_manifest(
        image_reference=manifest_oci_image_ref,
        absent_ok=absent_ok,
    )
    if not manifest and absent_ok:
        return None
    elif not manifest and not absent_ok:
        raise ValueError(f'did not find component-descriptor at {manifest_oci_image_ref=}')

    # XXX after "full" migration to v2, rm fallback coding below
    try:
        cfg_dict = json.loads(
            container.registry.retrieve_blob(
                image_reference=manifest_oci_image_ref,
                digest=manifest.config.digest,
            ).decode('utf-8')
        )
        cfg = dacite.from_dict(
            data_class=gci.oci.ComponentDescriptorOciCfg,
            data=cfg_dict,
        )
        layer_digest = cfg.componentDescriptorLayer.digest
    except Exception as e:
        print(f'Warning: failed to parse or retrieve component-descriptor-cfg: {e=}')
        print('falling back to single-layer')

        # by contract, there must be exactly one layer (tar w/ component-descriptor)
        if not (layers_count := len(manifest.layers) == 1):
            print(f'XXX unexpected amount of {layers_count=}')

        layer_digest = manifest.layers[0].digest

    blob_bytes = container.registry.retrieve_blob(
        image_reference=manifest_oci_image_ref,
        digest=layer_digest,
    )
    # wrap in fobj
    blob_fobj = io.BytesIO(blob_bytes)
    component_descriptor = gci.oci.component_descriptor_from_tarfileobj(
        fileobj=blob_fobj,
    )
    return component_descriptor


def resolve_dependency(
    component: gci.componentmodel.Component,
    component_ref: gci.componentmodel.ComponentReference,
    repository_ctx_base_url=None,
    cache_dir: str=None,
):
    '''
    resolves the given component version. for migration purposes, there is a fallback in place

    - the component version is searched in the component's current ctx-repo
      if it is found, it is retrieved and returned
    - otherwise (not found), the component version is looked-up using v1-schema semantics
      (i.e. retrieve from github)
    - if it is found in github, it is retrieved, converted to v2, published to the component's
      current ctx-repository, and then returned
    '''
    if component_ref:
        cname = component_ref.componentName
        cversion = component_ref.version
    else:
        cname = component.name
        cversion = component.version

    # retrieve, if available
    component_descriptor = download_component_descriptor_v2(
        component_name=cname,
        component_version=cversion,
        ctx_repo_base_url=repository_ctx_base_url or component.current_repository_ctx().baseUrl,
        cache_dir=cache_dir,
        absent_ok=False,
    )
    return component_descriptor


def resolve_dependencies(
    component: gci.componentmodel.Component,
    include_component=True,
    cache_dir=None,
):
  if include_component:
    yield component
  print(f'resolving dependencies for {component.name=} {component.version=}')
  for component_ref in component.componentReferences:
    print(f'resolving {component_ref=}')
    resolved_component_descriptor = resolve_dependency(
      component=component,
      component_ref=component_ref,
      cache_dir=cache_dir,
    )
    yield resolved_component_descriptor.component
    # XXX consider not resolving recursively, if immediate dependencies are present in ctx
    yield from resolve_dependencies(
        component=resolved_component_descriptor.component,
        include_component=False,
        cache_dir=cache_dir,
    )
  # if this line is reached, all dependencies could successfully be resolved


def rm_component_descriptor(
    component: gci.componentmodel.Component,
    recursive=True,
):
    target_ref = _target_oci_ref(
        component=component,
        component_ref=component,
    )

    if recursive:
        for component_ref in component.componentReferences:
            component_descriptor = resolve_dependency(
                component,
                component_ref,
                repository_ctx_base_url=None,
            )
            rm_component_descriptor(
                component=component_descriptor.component,
                recursive=recursive,
            )

    container.registry.rm_tag(image_reference=target_ref)


def components(
    component_descriptor_v2: gci.componentmodel.ComponentDescriptor,
    cache_dir: str=None,
    _visited_component_versions: typing.Tuple[str, str]=(),
):
    component = component_descriptor_v2.component
    yield component

    new_visited_component_versions = _visited_component_versions + \
        (component.name, component.version) + \
        tuple((cref.componentName, cref.version) for cref in component.componentReferences)

    for component_ref in component.componentReferences:
        cref_version = (component_ref.componentName, component_ref.version)
        if cref_version in _visited_component_versions:
            continue

        component_descriptor_v2 = resolve_dependency(
            component=component,
            component_ref=component_ref,
            cache_dir=cache_dir,
        )
        yield from components(
            component_descriptor_v2=component_descriptor_v2,
            cache_dir=cache_dir,
            _visited_component_versions=new_visited_component_versions,
        )


class ResourceFilter(enum.Enum):
    LOCAL = 'local'
    EXTERNAL = 'external'
    ALL = 'all'


class ResourcePolicy(enum.Enum):
    IGNORE_NONMATCHING_ACCESS_TYPES = 'ignore_nonmatching_access_types'
    WARN_ON_NONMATCHING_ACCESS_TYPES = 'warn_on_nonmatching_access_types'
    FAIL_ON_NONMATCHING_ACCESS_TYPES = 'fail_on_nonmatching_access_types'


def resources(
    component: gci.componentmodel.Component,
    resource_access_types: typing.Iterable[gci.componentmodel.AccessType],
    resource_types: typing.Iterable[gci.componentmodel.ResourceType]=None,
    resource_filter: ResourceFilter=ResourceFilter.ALL,
    resource_policy: ResourcePolicy=ResourcePolicy.FAIL_ON_NONMATCHING_ACCESS_TYPES,
):
    if resource_filter is ResourceFilter.LOCAL:
        resources = [r for r in component.resources if r.relation is cm.ResourceRelation.LOCAL]
    elif resource_filter is ResourceFilter.EXTERNAL:
        resources = [r for r in component.resources if r.relation is cm.ResourceRelation.EXTERNAL]
    elif resource_filter is ResourceFilter.ALL:
        resources = component.resources
    else:
        raise NotImplementedError

    for resource in (r for r in resources if resource_types is None or r.type in resource_types):
        if resource.access is None and None in resource_access_types:
            yield resource
        elif resource.access and resource.access.type in resource_access_types:
            yield resource
        else:
            if resource_policy is ResourcePolicy.IGNORE_NONMATCHING_ACCESS_TYPES:
                continue
            elif resource_policy is ResourcePolicy.WARN_ON_NONMATCHING_ACCESS_TYPES:
                ci.util.warning(
                    f"Skipping resource with unhandled access type '{resource.access.type}'"
                )
                continue
            elif resource_policy is ResourcePolicy.FAIL_ON_NONMATCHING_ACCESS_TYPES:
                raise ValueError(resource)
            else:
                raise NotImplementedError


def enumerate_oci_resources(
    component_descriptor,
    cache_dir: str=None,
):
    for component in components(component_descriptor, cache_dir=cache_dir):
        for resource in resources(
            component=component,
            resource_types=[gci.componentmodel.ResourceType.OCI_IMAGE],
            resource_access_types=[gci.componentmodel.AccessType.OCI_REGISTRY],
        ):
            yield (component, resource)


def greatest_references(
    references: typing.Iterable[gci.componentmodel.ComponentReference],
) -> gci.componentmodel.ComponentReference:
    '''
    yields the component references from the specified iterable of ComponentReference that
    have the greates version (grouped by component name).
    Id est: if the sequence contains exactly one version of each contained component name,
    the sequence is returned unchanged.
    '''
    references = tuple(references)
    names = [r.name for r in references]

    for name in names:
        matching_refs = [r for r in references if r.name == name]
        if len(matching_refs) == 1:
            # in case reference name was unique, do not bother sorting
            # (this also works around issues from non-semver versions)
            yield matching_refs[0]
        else:
            # there might be multiple component versions of the same name
            # --> use the greatest version in that case
            matching_refs.sort(key=lambda r: version.parse_to_semver(r.version))
            # greates version comes last
            yield matching_refs[-1]


def latest_component_version(component_name: str, ctx_repo_base_url: str) -> str:
    oci_image_repo = _target_oci_repository_from_component_name(component_name, ctx_repo_base_url)
    image_tags = container.registry.ls_image_tags(oci_image_repo)
    return version.find_latest_version(image_tags)


def greatest_component_version_with_matching_minor(
    component_name: str,
    ctx_repo_base_url: str,
    reference_version: str,
) -> str:
    oci_image_repo = _target_oci_repository_from_component_name(component_name, ctx_repo_base_url)
    image_tags = container.registry.ls_image_tags(oci_image_repo)
    return version.find_latest_version_with_matching_minor(
        reference_version=reference_version,
        versions=image_tags,
    )
