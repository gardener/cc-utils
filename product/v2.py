'''
utils used for transitioning to new component-descriptor v2

see: https://github.com/gardener/component-spec
'''

import gci.componentmodel as cm

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
