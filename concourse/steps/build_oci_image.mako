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
is_platform_aware = bool(publish_trait.platforms())
image_descriptor = job_step._extra_args['image_descriptor']
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

version_path = os.path.join(job_step.input('version_path'), 'version')

eff_version_replace_token = '${EFFECTIVE_VERSION}'

oci_builder = publish_trait.oci_builder()
need_qemu = True

if platform and (worker_node_tags := job_step.worker_node_tags):
  concourse_cfg = cfg_set.concourse()
  node_cfg = concourse_cfg.worker_node_cfg
  if worker_platform := node_cfg.platform_for_oci_platform(
    oci_platform_name=platform,
    absent_ok=True,
  ):
    if worker_platform.worker_tag in worker_node_tags:
      need_qemu = False
    normalised_platform = worker_platform.normalised_oci_platform_name
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

image_ref = f'${image_ref}:{image_tag}'

${step_lib('build_oci_image')}

% if oci_builder is cm_publish.OciBuilder.KANIKO:
home = '/kaniko'
docker_cfg_dir = os.path.join(home, '.docker')
os.makedirs(docker_cfg_dir, exist_ok=True)
docker_cfg_path = os.path.join(docker_cfg_dir, 'config.json')

write_docker_cfg(
    dockerfile_path='${dockerfile_relpath}',
    docker_cfg_path=docker_cfg_path,
)

subproc_env = os.environ.copy()
subproc_env['HOME'] = home
subproc_env['GOOGLE_APPLICATION_CREDENTIALS'] = docker_cfg_path
subproc_env['PATH'] = '/kaniko/bin'
subproc_env['EFFECTIVE_VERSION'] = effective_version

image_outfile = '${image_descriptor.name()}.oci-image.tar'

# XXX rm migration-code again
if os.path.exists('/kaniko/executor'):
  kaniko_executor = '/kaniko/executor'
else:
  kaniko_executor = '/bin/kaniko'

# XXX another hack: save truststores from being purged by kaniko's multistage-build
import certifi
os.link(
  (certifi_certs_path := certifi.where()),
  (certifi_bak := os.path.join('/', 'kaniko', 'cacert.pem'))
)
os.link(
  (ca_certs_path := os.path.join('/', 'etc', 'ssl', 'certs', 'ca-certificates.crt')),
  (ca_certs_bak := os.path.join('/', 'kaniko', 'ca-certificates.crt')),
)

## Do not install logging hander to oci_client here as there is currently an issue with
## the cfg-set-caching we've added for our kaniko build leading to confusing (to our users)
## build-logs in case of errors
oci_client = ccc.oci.oci_client(install_logging_handler=False)

## one last hack: import concourse-config upfront
## (for some reason, this type will not be found, even after restoring python's libdir)
import model.concourse
concourse_cfg = model.concourse.ConcourseConfig

new_root = mv_directories_to_kaniko_dir()
kaniko_executor = os.path.join(new_root, kaniko_executor[1:])

kaniko_argv = (
  kaniko_executor,
  '--force',
  '--no-push',
  '--dockerfile', '${dockerfile_relpath}',
  '--context', '${build_ctx_dir}',
  '--tarPath', image_outfile,
  '--destination', image_ref,
% if (target := image_descriptor.target_name()):
  '--target', '${target}',
% endif
)

for key in build_args:
  kaniko_argv += '--build-arg', f'{key}={build_args[key]}'

logger.info(f'running kaniko-build {kaniko_argv=}')

res = subprocess.run(
  kaniko_argv,
  env=subproc_env,
  check=True,
)

restore_required_dirs(root_dir=new_root)

logger.info(f'wrote image {image_ref=} to {image_outfile=} attempting to push')

os.makedirs(os.path.dirname(certifi_certs_path), exist_ok=True)
if not os.path.exists(certifi_certs_path):
  os.link(certifi_bak, certifi_certs_path)

os.makedirs(os.path.dirname(ca_certs_path), exist_ok=True)
if not os.path.exists(ca_certs_path):
  os.link(ca_certs_bak, ca_certs_path)

additional_tags = ${image_descriptor.additional_tags()}

print(f'publishing to {image_ref=}, {additional_tags=}')

manifest_mimetype = om.DOCKER_MANIFEST_SCHEMA_V2_MIME

oci.publish_container_image_from_kaniko_tarfile(
  image_tarfile_path=image_outfile,
  oci_client=oci_client,
  image_reference=image_ref,
  additional_tags=additional_tags,
  manifest_mimetype=manifest_mimetype,
)
% elif oci_builder in (cm_publish.OciBuilder.DOCKER, cm_publish.OciBuilder.DOCKER_BUILDX):
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
% if (target := image_descriptor.target_name()):
    '--target', '${target}',
% endif
    '--tag', image_ref,
% for img_ref in additional_img_refs:
    '--tag', '${img_ref}',
% endfor
  '--file', '${dockerfile_relpath}',
  '${build_ctx_dir}',
)

env = os.environ.copy()
env['EFFECTIVE_VERSION'] = effective_version
% if oci_builder is cm_publish.OciBuilder.DOCKER and publish_trait.use_buildkit():
env['DOCKER_BUILDKIT'] = '1'
% endif

logger.info(f'running docker-build with {docker_argv=}')
subprocess.run(
  docker_argv,
  check=True,
  env=env,
)

for img_ref in (image_ref, *${additional_img_refs}):
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

% else:
  <% raise NotImplementedError(oci_builder) %>
% endif
</%def>
