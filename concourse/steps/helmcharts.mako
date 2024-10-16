<%def name="helmcharts_step(job_step, job_variant, indent)", filter="indent_func(indent),trim">
<%
from makoutil import indent_func
import concourse.model.traits.publish
import collections
import os

publish_trait = job_variant.trait('publish')
helmchart_cfgs: tuple[concourse.model.traits.publish.HelmchartCfg] = publish_trait.helmcharts

main_repo_relpath = job_variant.main_repository().resource_name()
effective_version_path = os.path.join(job_step.input('version_path'), 'version')
%>
import datetime
import hashlib
import json
import logging
import os
import pprint
import subprocess
import tempfile

import jsonpath_ng
import yaml

import ccc.oci
import ocm
import oci.model
import concourse.steps.component_descriptor_util as cdu
import oci.model

logger = logging.getLogger('helmcharts.step')

with open('${effective_version_path}') as f:
  effective_version = f.read().strip()

component_descriptor = cdu.component_descriptor_from_dir(
  '${job_step.input('component_descriptor_dir')}',
)
component = component_descriptor.component

logger.info("Publishing ${len(helmchart_cfgs)} helmchart(s):")
% for helmchart_cfg in helmchart_cfgs:
logger.info("${helmchart_cfg}")
print()
% endfor

oci_client = ccc.oci.oci_client()

% for helmchart_cfg in helmchart_cfgs:
logger.info('Chart: {helmchart_cfg.name}')
print()
helmchart_dir = os.path.join('${main_repo_relpath}', '${helmchart_cfg.dir}')
helmchart_name = '${helmchart_cfg.name}'
print(f'{helmchart_dir=} {helmchart_name=}')
if not os.path.isdir(helmchart_dir):
  logger.error(f'not an existing directory: {helmchart_dir=}')
  exit(1)
helmchart_outdir = 'helmchart-archives.d'
if not os.path.exists(helmchart_outdir):
  os.mkdir(helmchart_outdir)

# preprocess helmchart
# - overwrite helmchart-name according to pipeline-cfg (to ensure consistency between content
#   and target-ref
# - overwrite values in values.yaml (in particular to inject image-references from current build)
values_yaml_path = os.path.join(helmchart_dir, 'values.yaml')
with open(values_yaml_path) as f:
  values = yaml.safe_load(f)

def find_resource(name: str):
  for resource in component.resources:
    if not resource.type is ocm.ArtefactType.OCI_IMAGE:
      continue
    if resource.name == name:
      return resource
  logger.error(f'did not find resource with name {name} in component-descriptor')
  exit(1)

%  for mapping_cfg in helmchart_cfg.mappings:
image_name, image_attr = ${mapping_cfg.referenced_resource_and_attribute}
resource = find_resource(name=image_name)

image_ref = oci.model.OciImageReference(resource.access.imageReference)
if image_attr == 'repository':
  value = image_ref.ref_without_tag
elif image_attr == 'tag':
  value = image_ref.tag
elif image_attr == 'image':
  value = str(image_ref)

attribute = '${mapping_cfg.attribute}'
attribute_path = jsonpath_ng.parse(attribute)

logger.info(f'resolved ${mapping_cfg.ref} to {value}. Patching values.yaml')
attribute_path.update_or_create(values, value)
%  endfor

with open(values_yaml_path, 'w') as f:
  yaml.safe_dump(values, f)

charts_yaml = os.path.join(helmchart_dir, 'Chart.yaml')
with open(charts_yaml) as f:
  charts_yaml_values = yaml.safe_load(f)

if charts_yaml_values.get('name') != helmchart_name:
  logger.warning(f'{charts_yaml}.name differs from {helmchart_name} - patching')
  charts_yaml_values['name'] = helmchart_name

with open(charts_yaml, 'w') as f:
  yaml.safe_dump(charts_yaml_values, f)

helm_package_argv = [
  'helm',
  'package',
  helmchart_dir,
  '--destination', helmchart_outdir,
  '--version', effective_version,
]
print(helm_package_argv)

subprocess.run(
  helm_package_argv,
  check=True,
)

helmchart_archive_path = os.path.join(
  helmchart_outdir,
  f'{helmchart_name}-{effective_version}.tgz',
)
if not os.path.isfile(helmchart_archive_path):
  logger.error(f'not an existing file: {helmchart_archive_path=}')
  exit(1)

target_ref = f'${helmchart_cfg.registry}/{helmchart_name}:{effective_version}'
logger.info(f'Publishing helmchart to {target_ref=}')
with open(helmchart_archive_path, 'rb') as f:
  sha256 = hashlib.sha256()
  leng = 0
  while (chunk := f.read(4096)):
    leng += len(chunk)
    sha256.update(chunk)

  f.seek(0)

  oci_client.put_blob(
    image_reference=target_ref,
    digest=(digest := f'sha256:{sha256.hexdigest()}'),
    octets_count=leng,
    data=f,
  )

cfg_blob_bytes = json.dumps(charts_yaml_values).encode('utf-8')
cfg_blob_digest = f'sha256:{hashlib.sha256(cfg_blob_bytes).hexdigest()}'
cfg_blob_leng = len(cfg_blob_bytes)

oci_client.put_blob(
  image_reference=target_ref,
  digest=cfg_blob_digest,
  octets_count=cfg_blob_leng,
  data=cfg_blob_bytes,
)

isonow = datetime.datetime.now(tz=datetime.timezone.utc).isoformat(timespec='seconds')
isonow = isonow.replace('+00:00', 'Z') # match format used by helm
manifest = oci.model.OciImageManifest(
  annotations={
    'org.opencontainers.image.created': isonow,
    'org.opencontainers.image.description': charts_yaml_values.get(
      'description',
      'no description available',
    ),
    'org.opencontainers.image.title': helmchart_name,
    'org.opencontainers.image.version': effective_version,
  },
  config=oci.model.OciBlobRef(
    digest=cfg_blob_digest,
    mediaType='application/vnd.cncf.helm.config.v1+json',
    size=cfg_blob_leng,
  ),
  layers=[oci.model.OciBlobRef(
    digest=digest,
    mediaType='application/vnd.cncf.helm.chart.content.v1.tar+gzip',
    size=leng,
  )],
  mediaType='application/vnd.oci.image.manifest.v1+json',
)

oci_client.put_manifest(
  image_reference=target_ref,
  manifest=json.dumps(manifest.as_dict()).encode('utf-8'),
)
logger.info(f'published helmchart to {target_ref=}')
print()

## dump mapping as suggested by OCM-CLI:
## https://github.com/open-component-model/ocm/blob/2b9ed814dee16e351636cb0d4ea0203f72224c0d/components/helmdemo/README.md
mapping_outpath = 'helmcharts/${helmchart_cfg.name}.mapping.json'
print(f'writing  mapping to {mapping_outpath}')
<%
# <resource-name>: {tag: attr, repository: attr, image: attr}
attrs_by_resource = collections.defaultdict(dict)
for mapping in helmchart_cfg.mappings:
  resource_name, ref_name = mapping.referenced_resource_and_attribute
  attrs_by_resource[resource_name][ref_name] = mapping.attribute
%>
mappings = []
%  for resource_name, attribute_mappings in attrs_by_resource.items():
mappings.append({
  'resource': {'name': '${resource_name}'},
%   for ref, attr in attribute_mappings.items():
  '${ref}': '${attr}',
%   endfor
})
%  endfor
mapping_root = {
  'imageMapping': mappings,
  'helmchartResource': {
    'name': '${helmchart_cfg.name}',
  },
}
pprint.pprint(mapping_root)
with open(mapping_outpath, 'w') as f:
  json.dump(mapping_root, f)
% endfor
</%def>
