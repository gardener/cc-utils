<%def name="publish_step(job_step, job_variant, indent)", filter="indent_func(indent),trim">
<%
from makoutil import indent_func
import collections
import os

import model.concourse

publish_trait = job_variant.trait('publish')

if not (platforms := publish_trait.platforms()):
  noop = True
else:
  noop = False

version_path = os.path.join(job_step.input('version_path'), 'version')
eff_version_replace_token = '${EFFECTIVE_VERSION}'
image_descriptors = publish_trait.dockerimages()

# collect tags for multiarch "imagelist" and variant-images
# (e.g.: {'image:1.2.3': {'my-image:1.2.3-linux/x86_64', 'my-image:1.2.3-linux/arm64'}}
# note:
# - '/' need to be replaced w/ '-' (done redundantly in build_oci_image.mako)
# - tag_templates need to be evaluated (at runtime)
image_ref_groups = collections.defaultdict(set)
extra_tags = collections.defaultdict(set)
for image_descriptor in image_descriptors:
  base_image_ref_template = f'{image_descriptor.image_reference()}:{image_descriptor.tag_template()}'

  if image_descriptor._platform:
    normalised_platform = model.concourse.Platform.normalise_oci_platform_name(
      image_descriptor._platform
    )
    specific_tag = base_image_ref_template + f'-{normalised_platform.replace("/", "-")}'
  else:
    specific_tag = base_image_ref_template

  image_ref_groups[base_image_ref_template].add(specific_tag)
  for tag in image_descriptor.additional_tags():
    extra_tags[base_image_ref_template].add(tag)
%>

import logging
import json
import pprint
import sys

logger = logging.getLogger('publish.step')

if ${noop}:
  logger.info('this is a dummy-step - exiting now')
  sys.exit(0)

with open('${version_path}') as f:
  effective_version = f.read().strip()

def eval_tag_template(template: str):
  return template.replace('${eff_version_replace_token}', effective_version)

import hashlib

import ccc.oci
import oci.model as om
oci_client = ccc.oci.oci_client()

def to_manifest_list_entry(image_ref_template: str, oci_client=oci_client):
  image_reference = eval_tag_template(template=image_ref_template)
  manifest_raw = oci_client.manifest_raw(image_reference).content
  manifest_digest = f'sha256:{hashlib.sha256(manifest_raw).hexdigest()}'
  manifest_size = len(manifest_raw)

  manifest = oci_client.manifest(image_reference)
  cfg_blob = oci_client.blob(image_reference, manifest.config.digest).json()

  os_id = cfg_blob.get('os', 'linux')
  if isinstance(os_id, dict):
    os_id = 'linux' # hardcode fallback to linux

  arch = cfg_blob['architecture']

  return om.OciImageManifestListEntry(
    digest=manifest_digest,
    size=manifest_size,
    mediaType=manifest.mediaType,
    platform=om.OciPlatform(
      architecture=arch,
      os=os_id,
      variant=cfg_blob.get('variant', None),
      features=cfg_blob.get('features', []),
    ),
  )

% for target_ref, variant_refs in image_ref_groups.items():
target_ref = eval_tag_template(template='${target_ref}')
manifest_list = om.OciImageManifestList(manifests=[
  to_manifest_list_entry(img_ref_template) for img_ref_template in ${variant_refs}
])
manifest_bytes = json.dumps(manifest_list.as_dict()).encode('utf-8')

manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
logger.info(f'publishing image-list: {target_ref=} | {manifest_digest=}')
pprint.pprint(manifest_list.as_dict())

oci_client.put_manifest(image_reference=target_ref, manifest=manifest_bytes)
%   for extra_tag in extra_tags[target_ref]:
image_ref = om.OciImageReference(
  eval_tag_template(template='${target_ref}')
).ref_without_tag + ':${extra_tag}'
oci_client.put_manifest(image_reference=image_ref, manifest=manifest_bytes)
%   endfor
% endfor
</%def>
