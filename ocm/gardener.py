'''
this module contains code specific to how Gardener-Components (https://github.com/gardener)
define cross-references to resources to other (Gardener-)Components.

Code in this module is not intended for re-use.
'''

import collections.abc
import copy
import dataclasses
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


def find_upgrade_vector(
    component_id: ocm.ComponentIdentity,
    version_lookup: ocm.VersionLookup,
    ignore_prerelease_versions=True,
    ignore_invalid_semver_versions=True,
) -> UpgradeVector | None:
    '''
    returns an upgrade-vector from given component_id to greatest available (using semver-arithmetic)
    based on passed version-lookup's returned versions.

    If no greater version can be found, None is returned.
    '''
    greatest_version = version.greatest_version(
        versions=version_lookup(component_id),
        ignore_prerelease_versions=ignore_prerelease_versions,
        invalid_semver_ok=ignore_prerelease_versions,
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
    greatest_crefs = {} # cname -> cref

    for reference in references:
        cid = reference.component_id
        if cid.name in greatest_crefs:
            candidate_version = version.parse_to_semver(cid.version)
            have_version = version.parse_to_semver(greatest_crefs[cid.name].component_id.version)

            if candidate_version > have_version:
                greatest_crefs[cid.name] = reference
        else:
            greatest_crefs[cid.name] = reference

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
        value={'images': []}
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
        img_repo = oci.model.OciImageReference(resource.access.imageReference).ref_without_tag
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

      cref_images_label = component_reference.find_label(name='imagevector.gardener.cloud/images')

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
      version=tag,
      extraIdentity=extra_identity,
      labels=labels,
      relation=relation,
      type=ocm.ArtefactType.OCI_IMAGE,
      access=ocm.OciAccess(
        type=ocm.AccessType.OCI_REGISTRY,
        imageReference=f'{img_repo}:{tag}',
      )
    )

    component.resources.append(img_resource)

  if not imagevector_label.value['images']:
    component.labels.remove(imagevector_label)

  return component
