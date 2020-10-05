'''
utils used for transitioning to new component-descriptor v2

see: https://github.com/gardener/component-spec
'''

import io
import itertools

import gci.componentmodel as cm
import gci.oci

import ccc.cfg
import ci.util
import container.registry
import product.model
import product.util


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

    for local_resource in component_v2.localResources:
        if local_resource.type is cm.ResourceType.OCI_IMAGE:
            component_deps.add_container_image_dependency(
                product.model.ContainerImage.create(
                    name=local_resource.name,
                    version=local_resource.version,
                    image_reference=local_resource.access.imageReference,
                    relation=product.model.Relation.LOCAL,
                )
            )
        elif local_resource.type is cm.ResourceType.GENERIC:
            component_deps.add_generic_dependency(
                product.model.GenericDependency.create(
                    name=local_resource.name,
                    version=local_resource.version,
                )
            )

    for external_resource in component_v2.externalResources:
        if external_resource.type is cm.ResourceType.OCI_IMAGE:
            component_deps.add_container_image_dependency(
                product.model.ContainerImage.create(
                    name=external_resource.name,
                    version=external_resource.version,
                    image_reference=external_resource.access.imageReference,
                    relation=product.model.Relation.THIRD_PARTY,
                )
            )
        elif external_resource.type is cm.ResourceType.GENERIC:
            component_deps.add_generic_dependency(
                product.model.GenericDependency.create(
                    name=external_resource.name,
                    version=external_resource.version,
                )
            )

    return component_v1


def _convert_dependencies_to_v2_resources(
    component_descriptor_v1: product.model.ComponentDescriptor,
    component_v1: product.model.Component,
    relation: product.model.Relation,
):
    '''
    calculates effective dependencies (for OCI image dependencies, only), and yields
    their component-descriptor v2-equivalents. Filters by component-relation
    '''

    for container_image in product.util._effective_images(
        component_descriptor=component_descriptor_v1,
        component=component_v1,
    ):
        if not container_image.relation() is relation:
            continue

        yield cm.Resource(
            name=container_image.name(),
            version=container_image.version(),
            type=cm.ResourceType.OCI_IMAGE,
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
            localResources=[
                resource for resource in
                _convert_dependencies_to_v2_resources(
                    component_descriptor_v1=component_descriptor_v1,
                    component_v1=component_v1,
                    relation=product.model.Relation.LOCAL,
                )
            ],
            externalResources=[
                resource for resource in
                _convert_dependencies_to_v2_resources(
                    component_descriptor_v1=component_descriptor_v1,
                    component_v1=component_v1,
                    relation=product.model.Relation.THIRD_PARTY,
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


def _target_oci_ref(
    component: gci.componentmodel.Component,
    component_ref: gci.componentmodel.ComponentReference=None,
):
    if component_ref is None:
        component_ref = component

    # last ctx-repo is target-repository
    last_ctx_repo = component.repositoryContexts[-1]
    base_url = last_ctx_repo.baseUrl

    component_name = component_ref.name.lower() # oci-spec allows only lowercase
    component_version = component_ref.version

    return _target_oci_ref_from_ctx_base_url(
        component_name=component_name,
        component_version=component_version,
        ctx_repo_base_url=base_url,
    )


def _target_oci_ref_from_ctx_base_url(
    component_name: str,
    component_version: str,
    ctx_repo_base_url: str,
):
    component_name = component_name.lower() # oci-spec allows only lowercase

    return ci.util.urljoin(
        ctx_repo_base_url,
        'component-descriptors',
        f'{component_name}:{component_version}',
    )


def upload_component_descriptor_v2_to_oci_registry(
    component_descriptor_v2: gci.componentmodel.ComponentDescriptor,
):
    ensure_is_v2(component_descriptor_v2)

    target_ref = _target_oci_ref(component_descriptor_v2.component)

    raw_fobj = gci.oci.component_descriptor_to_tarfileobj(component_descriptor_v2)

    # upload cd-blob
    cd_digest = container.registry.put_blob(
        target_ref,
        fileobj=raw_fobj,
        mimetype=container.registry.docker_http.MANIFEST_SCHEMA2_MIME,
    )
    dummy_cfg = io.BytesIO(b'{}')
    cfg_digest = container.registry.put_blob(
        target_ref,
        fileobj=dummy_cfg,
        mimetype=container.registry.docker_http.OCI_CONFIG_JSON_MIME,
    )

    manifest = container.registry.OciImageManifest(
        config=container.registry.OciBlobRef(
            digest=f'sha256:{cfg_digest}',
            mediaType=container.registry.docker_http.OCI_CONFIG_JSON_MIME,
            size=dummy_cfg.tell(),
        ),
        layers=[
            container.registry.OciBlobRef(
                digest=f'sha256:{cd_digest}',
                mediaType='application/tar',
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
    target_ref = _target_oci_ref(
        component=component,
        component_ref=component_ref,
    )

    # retrieve, if available
    component_descriptor = retrieve_component_descriptor_from_oci_ref(
        manifest_oci_image_ref=target_ref,
        absent_ok=True,
    )
    if component_descriptor:
        return component_descriptor

    # fallback: retrieve from github (will only work for github-components, obviously)
    cfg_factory = ccc.cfg.cfg_factory()

    resolver_v1 = product.util.ComponentDescriptorResolver(
        cfg_factory=cfg_factory,
    )
    component_ref_v1 = component_ref.name, component_ref.version

    component_descriptor_v1 = resolver_v1.retrieve_descriptor(component_ref_v1)

    # convert and publish
    if repository_ctx_base_url is None:
        repository_ctx_base_url = component.repositoryContexts[-1].baseUrl

    component_v1 = component_descriptor_v1.component(component_ref_v1)
    component_descriptor_v2 = convert_component_to_v2(
        component_descriptor_v1=component_descriptor_v1,
        component_v1=component_v1,
        repository_ctx_base_url=repository_ctx_base_url,
    )
    upload_component_descriptor_v2_to_oci_registry(component_descriptor_v2)
    print(f're-published component-descriptor v2 for {component_ref=}')
    return component_descriptor_v2


def resolve_dependencies(
    component: gci.componentmodel.Component,
):
  print(f'resolving dependencies for {component.name=} {component.version=}')
  for component_ref in component.componentReferences:
    print(f'resolving {component_ref=}')
    resolved_component_descriptor = resolve_dependency(
      component=component,
      component_ref=component_ref,
    )
    # XXX consider not resolving recursively, if immediate dependencies are present in ctx
    resolve_dependencies(component=resolved_component_descriptor.component)
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
    component_descriptor_v2: gci.componentmodel.ComponentDescriptor
):
    component = component_descriptor_v2.component
    yield component

    for component_ref in component.componentReferences:
        component_descriptor_v2 = resolve_dependency(
            component=component,
            component_ref=component_ref
        )
        yield from components(component_descriptor_v2=component_descriptor_v2)
