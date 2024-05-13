<%def
  name="build_oci_image_step(job_step, job_variant, cfg_set, indent)",
  filter="indent_func(indent),trim"
>
<%
import os

from makoutil import indent_func
from concourse.steps import step_lib
import concourse.model.traits.publish as cm_publish
import model.concourse
container_registry_cfgs = cfg_set._cfg_elements(cfg_type_name='container_registry')

publish_trait = job_variant.trait('publish')
image_descriptor: cm_pubtrait.PublishDockerImageDescriptor = job_step._extra_args['image_descriptor']
if platform := image_descriptor.platform():
  normalised_oci_platform_name = model.concourse.Platform.normalise_oci_platform_name(platform)
  platform_suffix = f'-{normalised_oci_platform_name}'.replace('/', '-')
else:
  normalised_oci_platform_name = ''
  platform_suffix = ''

image_ref = image_descriptor.image_reference()
additional_img_refs = set(
  f'{image_descriptor.image_reference()}:{t}{platform_suffix}'
  for t in image_descriptor.additional_tags()
)
extra_push_targets_without_tag = set()
for image_reference in image_descriptor.extra_push_targets:
  if image_reference.has_digest_tag:
    raise ValueError(f'must not set digest for extra-push-target: {image_reference=}')
  elif image_reference.has_symbolical_tag:
    additional_img_refs.add(str(image_reference) + platform_suffix)
  else:
    # image-references w/o tag are handled below (requires template-instantiation at runtime)
    extra_push_targets_without_tag.add(image_reference)

main_repo = job_variant.main_repository()
main_repo_relpath = main_repo.resource_name()

dockerfile_relpath = os.path.join(
  job_step.input('image_path'),
  image_descriptor.builddir_relpath() or '',
  image_descriptor.dockerfile_relpath()
)
build_ctx_dir = os.path.join(
  job_step.input('image_path'),
  image_descriptor.builddir_relpath() or '',
)
build_dir = job_step.input('image_path')

version_path = os.path.join(job_step.input('version_path'), 'version')

eff_version_replace_token = '${EFFECTIVE_VERSION}'

oci_builder = publish_trait.oci_builder()
if not oci_builder in (
  cm_publish.OciBuilder.DOCKER,
  cm_publish.OciBuilder.DOCKER_BUILDX,
):
  raise ValueError(f'not imlemented: {oci_builder=}')

need_qemu = True

if platform:
  concourse_cfg = cfg_set.concourse()
  node_cfg = concourse_cfg.worker_node_cfg
  if worker_platform := node_cfg.platform_for_oci_platform(
    oci_platform_name=platform,
    absent_ok=True,
  ):
    worker_node_tags = job_step.worker_node_tags
    if worker_platform.worker_tag is None and not worker_node_tags:
      # both worker and job are "default" - platforms match
      need_qemu = False
    elif worker_platform.worker_tag in worker_node_tags:
      need_qemu = False
%>
import json
import logging
import os
import subprocess

import ccc.oci
import oci
import oci.model as om
import oci.util as ou

import shutil

with open('${version_path}') as f:
  effective_version = f.read().strip()

image_tag = '${image_descriptor.tag_template()}'.replace(
  '${eff_version_replace_token}',
   effective_version
)

build_args = ${image_descriptor.build_args()}
if 'EFFECTIVE_VERSION' not in build_args: ## preserve existing value if explicitly given by user
  build_args['EFFECTIVE_VERSION'] = effective_version

% if platform:
image_tag += '-${normalised_oci_platform_name.replace("/", "-")}'
% endif

extra_push_targets = set()
% for image_reference in extra_push_targets_without_tag:
extra_push_targets.add(f'${str(image_reference)}:{image_tag}')
% endfor

${step_lib('build_oci_image')}

import tempfile

import ccc.oci
import dockerutil
import model.container_registry as mc
import model.concourse
import oci.auth as oa
import oci.workarounds as ow

dockerutil.launch_dockerd_if_not_running()

docker_cfg_dir = tempfile.mkdtemp()
write_docker_cfg(
    dockerfile_path='${dockerfile_relpath}',
    docker_cfg_path=f'{docker_cfg_dir}/config.json',
)

%   if oci_builder is cm_publish.OciBuilder.DOCKER_BUILDX and need_qemu:
%     if normalised_oci_platform_name:
import platform
platform_name = model.concourse.Platform.normalise_oci_platform_name(
  f'{platform.system().lower()}/{platform.machine()}'
)
if platform_name == '${normalised_oci_platform_name}':
  need_qemu = False
else:
  need_qemu = True
%     else:
need_qemu = True
%     endif
if need_qemu:
  logger.info('preparing crossplatform build')
  prepare_qemu_and_binfmt_misc()
  logger.info('done preparing crossplatform build. Now running actual build')
else:
  logger.info('skipping setup of qemu - requested tgt platform matches local platform')
%   endif

% for target_spec in image_descriptor.targets:
print(40 * '=')
print("${f'{target_spec=}'}")
print()
image_name = '${target_spec.image}'
image_ref = f'{image_name}:{image_tag}'
additional_image_refs = []
% if image_descriptor.tag_as_latest():
additional_image_refs.append(f'{image_name}:latest')
% endif

docker_argv = (
  'docker',
  '--config', docker_cfg_dir,
  % if oci_builder is cm_publish.OciBuilder.DOCKER_BUILDX:
  'buildx',
  % endif
  'build',
  '--progress', 'plain',
)

for key in build_args:
  docker_argv += '--build-arg', f'{key}={build_args[key]}'

docker_argv += (
% if platform:
  '--platform', '${platform}',
% endif
% if (target := target_spec.target):
    '--target', '${target}',
% endif
    '--tag', image_ref,
  '--file', '${dockerfile_relpath}',
  '${build_ctx_dir}',
)

for additional_image_ref in additional_image_refs:
  docker_argv += ('--tag', additional_image_ref,)

## XXX: needs to be made "multi-target-aware"
for image_reference in extra_push_targets:
  docker_argv += (
    '--tag', str(image_reference),
  )

env = os.environ.copy()
env['EFFECTIVE_VERSION'] = effective_version
% if oci_builder is cm_publish.OciBuilder.DOCKER and publish_trait.use_buildkit():
env['DOCKER_BUILDKIT'] = '1'
% endif

% if prebuild_hook := image_descriptor.prebuild_hook:
prebuild_hook = '${prebuild_hook}'
logger.info(f'will run {prebuild_hook=}')
build_dir = os.path.abspath('${build_dir}')
dockerfile = os.path.abspath('${dockerfile_relpath}')
prebuild_env = os.environ.copy()
prebuild_env |= {
  'BUILD_DIR': build_dir,
  'DOCKERFILE': dockerfile,
}
subprocess.run(
  (os.path.join(build_dir, prebuild_hook),),
  check=True,
  env=prebuild_env,
)
logger.info('prebuild_hook succeeded - now running build')
% endif

logger.info(f'running docker-build with {docker_argv=}')
subprocess.run(
  docker_argv,
  check=True,
  env=env,
)

for img_ref in (image_ref, *additional_image_refs, *extra_push_targets):
  if not (container_registry_cfg := mc.find_config(
    image_reference=img_ref,
    privileges=oa.Privileges.READWRITE,
  )):
    raise RuntimeError(f'No container registry config found for {img_ref=} with write privilege.')

  docker_cfg_dir = tempfile.mkdtemp()
  with open(os.path.join(docker_cfg_dir, 'config.json'), 'w') as f:
    json.dump({'auths': container_registry_cfg.as_docker_auths()}, f)

  docker_argv = (
    'docker',
    '--config', docker_cfg_dir,
    'push',
    img_ref,
  )
  logger.info(f'running docker-push with {docker_argv=}')
  subprocess.run(docker_argv, check=True)

  ## in some cases, the built images are not accepted by all oci-registries
  ## (see oci.workarounds for details)
  oci_client = ccc.oci.oci_client()
  ow.sanitise_image(image_ref=img_ref, oci_client=oci_client)
% endfor

</%def>
