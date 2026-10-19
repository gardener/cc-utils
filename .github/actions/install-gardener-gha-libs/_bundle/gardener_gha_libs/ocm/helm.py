import copy
import jsonpath_ng

import oci.client
import oci.model as om
import ocm
import ocm.util


def find_resource(
    component: ocm.Component,
    component_ref_name: str | None,
    name: str,
    component_descriptor_lookup: ocm.ComponentDescriptorLookup | None=None,
    absent_ok: bool=False,
) -> ocm.Resource | None:
    '''
    Tries to resolve the OCI image resource with the given `name` in the provided `component`. If
    `component_ref_name` is specified as well (together with the lookup), the referenced
    sub-component is searched recursively to find the requested resource.
    '''
    if not component_ref_name or component_ref_name == component.name:
        for resource in component.resources:
            if resource.type is not ocm.ArtefactType.OCI_IMAGE:
                continue
            if resource.name != name:
                continue
            return resource

        if absent_ok:
            return None

        raise ValueError(f'did not find oci-image with {name=} in component {component.name}')

    if not component_descriptor_lookup:
        raise ValueError('component-descriptor-lookup is required if component-ref is set')

    for component_ref in component.componentReferences:
        component_ref = component_descriptor_lookup(component_ref.component_id).component

        if resource := find_resource(
            component=component_ref,
            component_ref_name=component_ref_name,
            name=name,
            component_descriptor_lookup=component_descriptor_lookup,
            absent_ok=True,
        ):
            return resource

    if absent_ok:
        return None

    raise ValueError(f'did not find oci-image with {name=} and {component_ref_name=}')


def localised_helmchart_values(
    component: ocm.Component,
    oci_client: oci.client.Client,
    resource_name: str,
    resource_version: str | None=None,
    resource_extra_id: dict[str, str] | None=None,
    resource_type: str='helmchart-imagemap',
    base_values: dict | None=None,
    component_descriptor_lookup: ocm.ComponentDescriptorLookup | None=None,
) -> dict:
    '''
    Resolves image references from a helmchart-imagemap resource and returns a dictionary
    with helm values
    '''
    for resource in component.resources:
        if resource_name != resource.name:
            continue
        if resource_version and resource_version != resource.version:
            continue
        if resource_type != resource.type:
            continue
        if resource_extra_id is not None and resource.extraIdentity != resource_extra_id:
            continue
        break
    else:
        raise ValueError(
            f'did not find resource with {resource_name=}, {resource_version=}, '
            f'{resource_type=}, {resource_extra_id=}'
        )

    if not resource.access.type is ocm.AccessType.LOCAL_BLOB:
        raise ValueError(f'{component.name}/{resource.name} has unexpected {resource.access.type=}')

    oci_ref = component.current_ocm_repo.component_version_oci_ref(component)

    image_mappings = oci_client.blob(
        image_reference=oci_ref,
        digest=resource.access.localReference,
        stream=False, # imagemaps are typically small, so it should be okay to read into memory
    ).json()['imageMapping']

    if not isinstance(image_mappings, list):
        cv = f'{component.name}:{component.version}'
        raise ValueError(f'imagemapping of {cv} does not match expected format')

    values = copy.deepcopy(base_values) if base_values else {}
    for image_mapping in image_mappings:
        # image-mapping is expected to contain the following attributes:
        #
        # component:
        #   name: optional-(sub-)component-name
        # resource:
        #   name: oci-image-resource-name (for looking up image-resource)
        # repository: <attribute-name to set resource's image-repository to>
        # tag: <attribute-name to set resource's image-tag to

        resource_name = image_mapping['resource']['name']
        component_ref_name = image_mapping.get('component', {}).get('name')

        resource = find_resource(
            component=component,
            component_ref_name=component_ref_name,
            name=resource_name,
            component_descriptor_lookup=component_descriptor_lookup,
        )

        access = ocm.util.to_absolute_oci_access(
            access=resource.access,
            ocm_repo=component.current_ocm_repo,
        )
        image_ref = om.OciImageReference(access.imageReference)

        if image_ref.has_mixed_tag:
            # special-handling, as OciImageReference will - for backwards-compatibility - always
            # return digest-tag for "mixed tags"
            symbolic_tag, digest_tag = image_ref.parsed_mixed_tag
            tag = f'{symbolic_tag}@{digest_tag}'
        else:
            tag = image_ref.tag

        jsonpath_values = {
            image_mapping['repository']: image_ref.ref_without_tag,
            image_mapping['tag']: tag,
        }

        # convert jsonpath-entries to nested dict
        for k,v in jsonpath_values.items():
            path = jsonpath_ng.parse(k)
            path.update_or_create(values, v)

    return values
