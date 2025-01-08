'''
temporary (hopefully short..) migration util as a backwards-compatible replacement for
productutil.py -> add_dependencies

different to the aforementioned cmd, an ocm-component-descriptor is expected as input, and
created as output
'''

import dataclasses
import sys
import yaml

import ocm

import ci.util

CliHint = ci.util.CliHint

parse = yaml.safe_load


def _raw_component_dep_to_v2(raw: dict):
  if not 'componentName' in raw:
    component_name = raw['name'].strip()
    name = component_name.replace('/', '_').replace('.', '_')
  else:
    component_name = raw['componentName'].strip()
    name = raw['name'].strip()

  version = raw['version'].strip()

  args = {
    'componentName': component_name,
    'name': name,
    'version': version,
  }

  if 'labels' in raw:
    args['labels'] = raw['labels']

  return ocm.ComponentReference(**args)


def _raw_image_dep_to_v2(raw: dict):
  img_ref = raw['image_reference']
  args = {
    'name': raw['name'],
    'version': raw['version'],
    'type': ocm.ArtefactType.OCI_IMAGE,
    'relation': ocm.ResourceRelation(raw.get('relation', ocm.ResourceRelation.EXTERNAL)),
    'access': ocm.OciAccess(type=ocm.AccessType.OCI_REGISTRY, imageReference=img_ref),
  }

  if 'labels' in raw:
    args['labels'] = raw['labels']

  return ocm.Resource(**args)


def add_dependencies(
  descriptor_src_file: str,
  component_name: str,
  component_version: str,
  descriptor_out_file: str=None,
  component_dependencies: [str]=[],
  container_image_dependencies: [str]=[],
):
  component_descriptor = ocm.ComponentDescriptor.from_dict(
    ci.util.parse_yaml_file(descriptor_src_file)
  )
  component = component_descriptor.component

  # perform sanity checks (component_name and version *must* match)
  if not component.name == component_name:
    raise ValueError(f'{component_name=} != {component.name=}')
  if not component.version == component_version:
    raise ValueError(f'{component_version=} != {component.version=}')

  component.componentReferences += [
    _raw_component_dep_to_v2(parse(cdep)) for cdep in component_dependencies
  ]
  for img_dep in container_image_dependencies:
    img_dep = parse(img_dep)
    img_res = _raw_image_dep_to_v2(img_dep)

    component.resources.append(img_res)

  if descriptor_out_file:
    outfh = open(descriptor_out_file, 'w')
  else:
    outfh = sys.stdout

  yaml.dump(
    data=dataclasses.asdict(component_descriptor),
    Dumper=ocm.EnumValueYamlDumper,
    stream=outfh,
  )
