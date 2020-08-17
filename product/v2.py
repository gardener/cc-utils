'''
utils used for transitioning to new component-descriptor v2

see: https://github.com/gardener/component-spec
'''

import io

import gci.componentmodel as cm
import gci.oci

import ci.util
import container.registry
import product.model
import product.util


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
                cm.ComponentReference(component.name(), component.version()) for component
                in component_v1.dependencies().components()
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
    component_descriptor_v2: gci.componentmodel.ComponentDescriptor,
):
    ensure_is_v2(component_descriptor_v2)
    component = component_descriptor_v2.component

    # last ctx-repo is target-repository
    last_ctx_repo = component.repositoryContexts[-1]
    base_url = last_ctx_repo.baseUrl

    component_name = component.name
    component_version = component.version

    return ci.util.urljoin(
        base_url,
        'component-descriptors',
        f'{component_name}:{component_version}',
    )


def upload_component_descriptor_v2_to_oci_registry(
    component_descriptor_v2: gci.componentmodel.ComponentDescriptor,
):
    ensure_is_v2(component_descriptor_v2)

    target_ref = _target_oci_ref(component_descriptor_v2)

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
