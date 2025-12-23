'''
this module contains code specific to how Gardener-Components (https://github.com/gardener)
define cross-references to resources to other (Gardener-)Components.

Code in this module is not intended for re-use.
'''

import collections.abc
import copy
import dataclasses
import datetime
import enum
import os

import dacite
import semver
import yaml

import oci.model
import ocm
import version


@dataclasses.dataclass
class ExtraComponentReference:
    component_reference: ocm.ComponentIdentity
    purpose: list[str] | None


@dataclasses.dataclass
class ExtraComponentReferencesLabel:
    name = 'ocm.software/ocm-gear/extra-component-references'
    value: list[ExtraComponentReference]


@dataclasses.dataclass
class UpgradeVector:
    whence: ocm.ComponentIdentity
    whither: ocm.ComponentIdentity

    @property
    def component_name(self) -> str:
        return self.whence.name

    @property
    def whence_version(self) -> semver.VersionInfo:
        return version.parse_to_semver(self.whence.version)

    @property
    def whither_version(self) -> semver.VersionInfo:
        return version.parse_to_semver(self.whither.version)

    @property
    def is_downgrade(self) -> bool:
        return self.whence_version > self.whither_version


class TemplateType(enum.StrEnum):
    JQ = 'jq'


@dataclasses.dataclass(kw_only=True)
class VersionTemplate:
    type: TemplateType = TemplateType.JQ
    expr: str

    @staticmethod
    def from_dict(raw: dict, /):
        return dacite.from_dict(
            data_class=VersionTemplate,
            data=raw,
            config=dacite.Config(
                cast=(enum.Enum,),
            ),
        )


def find_upgrade_vector(
    component_id: ocm.ComponentIdentity,
    version_lookup: ocm.VersionLookup,
    ignore_prerelease_versions: bool=True,
    ignore_invalid_semver_versions: bool=True,
) -> UpgradeVector | None:
    '''
    returns an upgrade-vector from given component_id to greatest available (using semver-arithmetic)
    based on passed version-lookup's returned versions.

    If no greater version can be found, None is returned.
    '''
    greatest_version = version.greatest_version(
        versions=version_lookup(component_id),
        ignore_prerelease_versions=ignore_prerelease_versions,
        invalid_semver_ok=ignore_invalid_semver_versions,
        min_version=component_id.version,
    )
    if not greatest_version:
        return None

    return UpgradeVector(
        whence=component_id,
        whither=dataclasses.replace(
            component_id,
            version=greatest_version,
        ),
    )


def iter_component_references(
    component: ocm.Component,
) -> collections.abc.Iterable[ocm.ComponentReference]:
    '''
    an opinionated function that will return component-references from both regular
    componentReferences-attribute of given component, as well as "soft-references" from
    `ExtraComponentReferencesLabel` (if present).
    '''
    yield from component.componentReferences
    if not (extra_ref_label := component.find_label(name=ExtraComponentReferencesLabel.name)):
        return

    for extra_ref in extra_ref_label.value:
        cref = dacite.from_dict(
            ExtraComponentReference,
            data=extra_ref,
        )

        yield ocm.ComponentReference(
            name=cref.component_reference.name,
            componentName=cref.component_reference.name,
            version=cref.component_reference.version,
        )


def iter_greatest_component_references(
    references: collections.abc.Iterable[ocm.ComponentReference],
) -> collections.abc.Iterable[ocm.ComponentReference]:
    '''
    Returns the greatest component references from the given iterable.
    We might have multiple component references for the same component name,
    but with different `name` attributes (e.g. for testing and for
    productive purposes), so this method groups the references by "<component_name>:<reference_name>"
    and returns the greatest version for each group.
    '''
    greatest_crefs = {} # "<component_name>:<reference_name>" -> cref

    for reference in references:
        cid = reference.component_id
        key = f'{cid.name}:{reference.name}'
        if key in greatest_crefs:
            candidate_version = version.parse_to_semver(cid.version)
            have_version = version.parse_to_semver(greatest_crefs[key].component_id.version)

            if candidate_version > have_version:
                greatest_crefs[key] = reference
        else:
            greatest_crefs[key] = reference

    yield from greatest_crefs.values()


def find_imagevector_file(
    repo_root: str=None,
) -> str | None:
    for candidate in (
        'internal/images/images.yaml', # etcd-druid
        'charts/images.yaml',
        'imagevector/images.yaml',
        'imagevector/containers.yaml',
        'imagevector/container.yaml',
    ):
        if repo_root:
            candidate = os.path.join(
                repo_root,
                candidate,
            )
        if os.path.isfile(candidate):
            return candidate


def iter_images_from_imagevector(
    images_yaml_path: str,
) -> collections.abc.Generator[dict, None, None]:
    with open(images_yaml_path) as f:
      for part in yaml.safe_load_all(f):
        yield from part['images']


def eval_version_template(
    version_template: VersionTemplate,
    image_dict: dict,
) -> str:
    '''
    evaluates given version-template and returns the yielded result (which is expected to be a
    single str)

    version_template is expected to have the following attributes:
        type: str # must be set to 'jq'
        expr: str # will be evaluated as jq-expression
    image_dict is the imagevector-entry that will be passed to jq
    '''
    # do late-import; jq is only used very rarely, so there is no need to unnecessarily break users
    # that do not have it installed
    import jq

    if not version_template.type is TemplateType.JQ:
        print(f'Error: type must equal `jq` - saw: {version_template=}')
        raise ValueError(version_template)

    prg = jq.compile(version_template.expr) # noqa: I1101
    prg = prg.input_value(image_dict)
    res = prg.all()
    if len(res) < 1:
        print(f'Error: {version_template.expr=} yielded no output')
        raise ValueError(version_template)
    if len(res) > 1:
        print(f'Error: {version_template.expr=} yielded more than just a single output: {res=}')
        raise ValueError(version_template)
    res, = res # we checked we have exactly one entry
    if not isinstance(res, str):
        print(f'Error: {version_template.expr=} did not yield a string: {res=}')
        raise ValueError(res)

    return res


def add_resources_from_imagevector(
    component: ocm.Component,
    images: collections.abc.Iterable[dict],
    component_prefixes: list[str],
    deduplicate_resources: bool=True,
) -> ocm.Component:
    '''
    deduplicate_resources: in concourse-case, resources built from pipeline are already present
    in (base-)component-descriptor. To remove those, set deduplicate_resources as True.
    in other cases (GitHub-Actions), where resources are not added redundantly, set to False.
    '''
    imagevector_label_name = 'imagevector.gardener.cloud/images'
    imagevector_label = component.find_label(imagevector_label_name)
    if not imagevector_label:
        component.labels.append(
            imagevector_label := ocm.Label(
                name=imagevector_label_name,
                value={'images': []},
            )
        )

    for image_dict in images:
        name = image_dict['name']
        resource_id = image_dict.get('resourceId', {'name': name})
        source_repo = image_dict.get('sourceRepository', None)
        img_repo = image_dict['repository']
        extra_identity = image_dict.get('extraIdentity', {})
        labels = copy.copy(image_dict.get('labels', []))
        tag = image_dict.get('tag', None)
        version = resource_id.get('version', tag) if resource_id else tag
        if (version_template := resource_id.get('version-template', None)):
            version = eval_version_template(
                version_template=VersionTemplate.from_dict(version_template),
                image_dict=image_dict,
            )
        target_version = image_dict.get('targetVersion', None)

        for prefix in (component_prefixes or ()):
            if img_repo.startswith(prefix):
                relation = 'local'
                is_local = True
                break
        else:
            relation = 'external'
            is_local = False

        resource_name = None
        resource = None
        if not tag:
            if resource_id:
                resource_name = resource_id['name']
                image_dict['name'] = resource_name
            else:
                resource_name = name

            for resource in component.resources:
                if resource.name != resource_name:
                    continue

                if resource.type != ocm.ArtefactType.OCI_IMAGE:
                    continue

                tag = resource.version
                # image-references from pipeline (base_component_descriptor) has precedence
                img_repo = oci.model.OciImageReference(
                    image_reference=resource.access.imageReference,
                ).ref_without_tag
                if deduplicate_resources:
                    component.resources.remove(resource)
                if not 'relation' in image_dict:
                    relation = resource.relation.value
                    pass
                break

        if not tag: # and not resource:
            # special-case: if there is no tag, the image is only added to `images`-label on
            # component-level
            imagevector_label.value['images'].append(image_dict)
            continue

        is_current_component = source_repo == component.name

        if not is_current_component and is_local:
            # if we have a tag, and repository is "local" (as passed-in via --component-prefixes),
            # then we add a component-reference, and add the image to a label of this reference
            for component_reference in component.componentReferences:
                if component_reference.name == name and component_reference.version == tag:
                  break
            else:
                component_reference = ocm.ComponentReference(
                  name=name,
                  componentName=source_repo,
                  version=tag,
                  extraIdentity=extra_identity,
                  labels=[ocm.Label(
                    name='imagevector.gardener.cloud/images',
                    value={'images': []},
                  )],
                )
                component.componentReferences.append(component_reference)

            cref_images_label = component_reference.find_label(
                name='imagevector.gardener.cloud/images',
            )

            cref_images_label.value['images'].append(
                image_dict | {'resourceId': resource_id}
            )
            continue

        labels.append({
            'name': 'imagevector.gardener.cloud/name',
            'value': name,
        })
        labels.append({
            'name': 'imagevector.gardener.cloud/repository',
            'value': img_repo,
        })
        if source_repo:
            labels.append({
                'name': 'imagevector.gardener.cloud/source-repository',
                'value': source_repo,
            })
        if target_version:
            labels.append({
                'name': 'imagevector.gardener.cloud/target-version',
                'value': target_version,
            })

        img_resource = ocm.Resource(
            name=resource_name or name,
            version=version or tag,
            extraIdentity=extra_identity,
            labels=labels,
            relation=relation,
            type=ocm.ArtefactType.OCI_IMAGE,
            access=ocm.OciAccess(
                type=ocm.AccessType.OCI_REGISTRY,
                imageReference=f'{img_repo}:{tag}',
            ),
        )

        component.resources.append(img_resource)

    if not imagevector_label.value['images']:
        component.labels.remove(imagevector_label)

    return component


def find_creation_time(
    component: ocm.Component,
) -> datetime.datetime | None:
    if creation_time := component.creationTime:
        return datetime.datetime.fromisoformat(creation_time)

    if label := component.find_label('cloud.gardener/ocm/creation-date'):
        return datetime.datetime.fromisoformat(label.value)

    return None


def find_matching_oci_resource(
    image: dict,
    resources: collections.abc.Iterable[ocm.Resource],
) -> ocm.Resource | None:
    '''
    returns (first) matching oci-image-resource, or None (if no such resource is found).

    `image` is expected to be a dict from `imagevector.gardener.cloud/images`['images'].

    For finding matching resource, `name` or the optional `resourceId.name` attributes are
    matched against resource.name. Resource of type !== ociImage are ignored.
    '''
    if (resource_id := image.get('resourceId', None)):
        resource_name = resource_id['name']
    else:
        resource_name = image['name']

    for resource in resources:
        if not resource.type is ocm.ArtefactType.OCI_IMAGE:
            continue
        if resource.name != resource_name:
            continue

        return resource


'''
an image-dict as read from `imagevector.gardener.cloud/images`.images (OCM-Label)
'''
ImageDict = dict
'''
an image-dict as understood by Gardener as an imagevector-overwrite. Similar to ImageDict, but
with attributes overwritten or augmented from OCM-Metadata.
'''
ImageOverwriteDict = dict
'''
a full imagevector-overwrite as understood by Gardener. Has a single attribute `images` containing
a list of `ImageOverwriteDict`s, sorted by `repository` attribute.
'''
ImageOverwrite = dict


def image_dict_from_image_dict_and_resource(
    component_name: str,
    image: ImageDict,
    resource: ocm.Resource,
) -> ImageOverwriteDict:
    '''
    creates an image-dict as understood by gardener as an imagevector-overwrite using an
    image-dict as read from `imagevector.gardener.cloud/images`-OCM-Label, and corresponding
    OCM-OCI-Image-Resource (use find_matching_oci_resource to determine valid inputs).
    '''
    image_ref = oci.model.OciImageReference(resource.access.imageReference)

    image_dict = {
        'name': image['name'],
        'repository': image_ref.ref_without_tag,
        'sourceRepository': component_name,
        'tag': image_ref.tag,
    }

    if (target_version := image.get('targetVersion')):
        image_dict['targetVersion'] = target_version
    if (labels := image.get('labels')):
        image_dict['labels'] = labels

    return image_dict


def oci_image_dict_from_resource(
    resource: ocm.Resource,
    resource_names_from_label: bool=True,
    fallback_to_target_version_from_resource: bool=False,
    resource_names: collections.abc.Iterable[str]=None,
) -> ImageOverwriteDict | None:
    '''
    returns an "image-dicts" as used for image-vector-overwrites understood by gardener for the
    given ocm.Resource, if the following conditions are met:

    - must be of type OciImage
    - must bear the `imagevector.gardener.cloud/name` label
    - if resource_names is passed, name must match (see below)

    By default, said label's value is used as value for `name` in resulting image-dict (this can
    be disabled by passing False for `resource_names_from_label`). The latter is done for
    "lss" (or "root") component.

    If `imagevector.gardener.cloud/target-version`-label is present, its value will be conveyed as
    `targetVersion`-attribute. If absent, the resource's `version` is used, if
    `fallback_to_target_version_from_resource` is passed as True.

    if resource_names is passed, only resources with matching names (honouring label, if configured)
    will be considered.
    '''
    if not resource.type is ocm.ArtefactType.OCI_IMAGE:
        return None

    if not (name_label := resource.find_label('imagevector.gardener.cloud/name')):
        return None

    if resource_names_from_label:
        name = name_label.value
    else:
        name = resource.name

    if resource_names is not None and not name in resource_names:
        return None

    image_ref = oci.model.OciImageReference(resource.access.imageReference)
    repository = image_ref.ref_without_tag

    image_dict = {
        'name': name,
        'repository': repository,
        'tag': image_ref.tag,
    }

    if (target_version_label := resource.find_label(
            'imagevector.gardener.cloud/target-version'
    )):
        image_dict['targetVersion'] = target_version_label.value
    elif fallback_to_target_version_from_resource:
        image_dict['targetVersion'] = resource.version

    return image_dict


def iter_image_dicts_from_image_dicts_and_resources(
    images: collections.abc.Iterable[ImageDict],
    component_name: str,
    resources: collections.abc.Iterable[ocm.Resource],
) -> collections.abc.Iterable[ImageOverwriteDict]:
    '''
    yields image-dicts as understood by gardener as an image-vector-overwrite from image-dicts
    as typically read from a `imagevector.gardener.cloud/images`-label (which in turn is
    typically read from a component-reference), updated from access-data read from passed-in
    resources (typically from referenced component).

    Typical use for this function:
    - read "images"-label from component-reference
    - resolve referenced component
    - pass images from label + resources from component
    '''
    for image in images:
        if not (resource := find_matching_oci_resource(
            image=image,
            resources=resources,
        )):
            continue

        yield image_dict_from_image_dict_and_resource(
            component_name=component_name,
            image=image,
            resource=resource,
        )


def iter_oci_image_dicts_from_component(
    component: ocm.Component,
    resource_names_from_label: bool,
    fallback_to_target_version_from_resource: bool,
    resource_names: collections.abc.Iterable[str],
    component_descriptor_lookup: ocm.ComponentDescriptorLookup,
) -> collections.abc.Iterable[ImageOverwriteDict]:
    for cref in component.componentReferences:
        if not (images_label := cref.find_label('imagevector.gardener.cloud/images')):
            continue

        # caveat: do not hide outer `component`
        inner_comp = component_descriptor_lookup(cref).component
        yield from iter_image_dicts_from_image_dicts_and_resources(
            images=images_label.value['images'],
            component_name=inner_comp.name,
            resources=inner_comp.resources,
        )

    for resource in component.resources:
        resource_dict = oci_image_dict_from_resource(
            resource=resource,
            resource_names_from_label=resource_names_from_label,
            fallback_to_target_version_from_resource=fallback_to_target_version_from_resource,
            resource_names=resource_names,
        )
        if resource_dict is None:
            continue
        yield resource_dict


def iter_oci_image_dicts_from_rooted_component(
    component: ocm.Component,
    root_component: ocm.Component | None,
    component_descriptor_lookup: ocm.ComponentDescriptorLookup,
) -> collections.abc.Iterable[ImageOverwriteDict]:
    component = component.component
    seen_image_keys = set() # (name, targetVersion)

    def image_dict_key(image_dict):
        return (image_dict['name'], image_dict.get('targetVersion', None))

    for image_dict in  iter_oci_image_dicts_from_component(
        component=component,
        resource_names_from_label=True,
        fallback_to_target_version_from_resource=False,
        resource_names=None,
        component_descriptor_lookup=component_descriptor_lookup,
    ):
        k = image_dict_key(image_dict)
        if k in seen_image_keys:
            continue
        seen_image_keys.add(k)
        yield image_dict

    if (images_label := component.find_label('imagevector.gardener.cloud/images')):
        resource_names = [i['name'] for i in images_label.value['images']]
    else:
        resource_names = []

    if not root_component:
        return

    for image_dict in iter_oci_image_dicts_from_component(
        component=root_component,
        resource_names_from_label=False,
        fallback_to_target_version_from_resource=True,
        resource_names=resource_names,
        component_descriptor_lookup=component_descriptor_lookup,
    ):
        k = image_dict_key(image_dict)
        if k in seen_image_keys:
            continue
        seen_image_keys.add(k)
        yield image_dict


def as_image_vector(
    images: collections.abc.Iterable[ImageOverwriteDict],
) -> ImageOverwrite:
    return {
        'images': sorted(
            images,
            key=lambda image_dict: image_dict['repository'],
        ),
    }


def image_vector_overwrite(
    component: ocm.Component,
    root_component: ocm.Component | None,
    component_descriptor_lookup: ocm.ComponentDescriptorLookup,
) -> ImageOverwrite:
    return as_image_vector(
        images=iter_oci_image_dicts_from_rooted_component(
            component=component,
            root_component=root_component,
            component_descriptor_lookup=component_descriptor_lookup,
        ),
    )
