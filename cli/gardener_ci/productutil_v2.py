'''
temporary (hopefully short..) migration util as a backwards-compatible replacement for
productutil.py -> add_dependencies

different to the aforementioned cmd, a component-descriptor-v2 is expected as input, and
created as output
'''

import dataclasses
import sys
import yaml

import gci.componentmodel as cm

import ci.util
import product.v2

CliHint = ci.util.CliHint

parse = yaml.safe_load


def _raw_component_dep_to_v2(raw: dict):
  if not 'componentName' in raw:
    component_name = raw['name']
    name = product.v2.mangle_name(component_name)
  else:
    component_name = raw['componentName']
    name = raw['name']

  args = {
    'componentName': component_name,
    'name': name,
    'version': raw['version'],
  }

  if 'labels' in raw:
    args['labels'] = raw['labels']

  return cm.ComponentReference(**args)


def _raw_image_dep_to_v2(raw: dict):
  img_ref = raw['image_reference']
  args = {
    'name': raw['name'],
    'version': raw['version'],
    'type': cm.ResourceType.OCI_IMAGE,
    'relation': cm.ResourceRelation(raw.get('relation', cm.ResourceRelation.EXTERNAL)),
    'access': cm.OciAccess(type=cm.AccessType.OCI_REGISTRY, imageReference=img_ref),
  }

  if 'labels' in raw:
    args['labels'] = raw['labels']

  return cm.Resource(**args)


def _raw_generic_dep_to_v2(raw: dict):
  name = raw['name']
  version = raw['version']
  rel = cm.ResourceRelation(raw.get('relation', cm.ResourceRelation.LOCAL))

  return cm.Resource(
    name=name,
    version=version,
    type=cm.ResourceType.GENERIC,
    relation=rel,
    access=None,
  )


def add_dependencies(
  descriptor_src_file: str,
  component_name: str,
  component_version: str,
  descriptor_out_file: str=None,
  component_dependencies: [str]=[],
  container_image_dependencies: [str]=[],
  generic_dependencies: [str]=[],
):
  component_descriptor = cm.ComponentDescriptor.from_dict(
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

  for gen_dep in generic_dependencies:
    gen_dep = parse(gen_dep)
    gen_res = _raw_generic_dep_to_v2(gen_dep)
    component.resources.append(gen_res)

  if descriptor_out_file:
    outfh = open(descriptor_out_file, 'w')
  else:
    outfh = sys.stdout

  yaml.dump(
    data=dataclasses.asdict(component_descriptor),
    Dumper=cm.EnumValueYamlDumper,
    stream=outfh,
  )
