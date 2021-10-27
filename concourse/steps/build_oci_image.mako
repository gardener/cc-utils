<%def
  name="build_oci_image_step(job_step, job_variant, cfg_set, indent)",
  filter="indent_func(indent),trim"
>
<%
import os

from makoutil import indent_func
from concourse.steps import step_lib
import concourse.model.traits.publish as cm_publish
container_registry_cfgs = cfg_set._cfg_elements(cfg_type_name='container_registry')

image_descriptor = job_step._extra_args['image_descriptor']
image_ref = image_descriptor.image_reference()
additional_img_refs = set(
  f'{image_descriptor.image_reference()}:{t}'
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

publish_trait = job_variant.trait('publish')
oci_builder = publish_trait.oci_builder()

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
% for k,v in image_descriptor.build_args().items():
  '--build-arg', '${k}=${v}',
% endfor
% if (target := image_descriptor.target_name()):
    '--target', '${target}',
% endif
)

logger.info(f'running kaniko-build {kaniko_argv=}')

res = subprocess.run(
  kaniko_argv,
  env=subproc_env,
  check=True,
)

restore_required_dirs(root_dir=new_root)

print(f'wrote image {image_ref=} to {image_outfile=} attempting to push')

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
% elif oci_builder is cm_publish.OciBuilder.DOCKER:
import tempfile

import dockerutil
import model.container_registry as mc
import oci.auth as oa

dockerutil.launch_dockerd_if_not_running()

docker_cfg_dir = tempfile.mkdtemp()
write_docker_cfg(
    dockerfile_path='${dockerfile_relpath}',
    docker_cfg_path=f'{docker_cfg_dir}/config.json',
)

docker_argv = (
  'docker',
  '--config', docker_cfg_dir,
  'build',
% for k,v in image_descriptor.build_args().items():
  '--build-arg', '${k}=${v}',
% endfor
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

logger.info(f'running docker-build with {docker_argv=}')
subprocess.run(
  docker_argv,
  check=True,
)

for img_ref in (image_ref, *${additional_img_refs}):
  container_registry_cfg = mc.find_config(
    image_reference=img_ref,
    privileges=oa.Privileges.READWRITE,
  )
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

% else:
  <% raise NotImplementedError(oci_builder) %>
% endif
</%def>
