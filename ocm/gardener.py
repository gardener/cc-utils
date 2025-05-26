'''
this module contains code specific to how Gardener-Components (https://github.com/gardener)
define cross-references to resources to other (Gardener-)Components.

Code in this module is not intended for re-use.
'''

import copy

import yaml

import oci.model
import ocm


def add_resources_from_imagevector(
    component: ocm.Component,
    images_yaml_path: str,
    component_prefixes: list[str],
) -> ocm.Component:
  # images_yaml_path, as found e.g. at github.com/gardener/gardener charts/images.yaml
  def iter_images():
    with open(images_yaml_path) as f:
      for part in yaml.safe_load_all(f):
        yield from part['images']

  imagevector_label_name = 'imagevector.gardener.cloud/images'
  imagevector_label = component.find_label(imagevector_label_name)
  if not imagevector_label:
    component.labels.append(
      imagevector_label := ocm.Label(
        name=imagevector_label_name,
        value={'images': []}
      )
    )

  for image_dict in iter_images():
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

        tag = resource.version
        # image-references from pipeline (base_component_descriptor) has precedence
        img_repo = oci.model.OciImageReference(resource.access.imageReference).ref_without_tag
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
