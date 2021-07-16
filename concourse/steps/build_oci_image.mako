<%def
  name="build_oci_image_step(job_step, job_variant, cfg_set, indent)",
  filter="indent_func(indent),trim"
>
<%
import os

from makoutil import indent_func
from concourse.steps import step_lib
container_registry_cfgs = cfg_set._cfg_elements(cfg_type_name='container_registry')

image_descriptor = job_step._extra_args['image_descriptor']

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


${step_lib('build_oci_image')}

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

with open('${version_path}') as f:
  effective_version = f.read().strip()

image_tag = '${image_descriptor.tag_template()}'.replace(
  '${eff_version_replace_token}',
   effective_version
)

image_ref = f'${image_descriptor.image_reference()}:{image_tag}'

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

oci_client = ccc.oci.oci_client()

## one last hack: import concourse-config upfront
## (for some reason, this type will not be found, even after restoring python's libdir)
import model.concourse
concourse_cfg = model.concourse.ConcourseConfig

new_root = mv_directories_to_kaniko_dir()
kaniko_executor = os.path.join(new_root, kaniko_executor[1:])

kaniko_argv = (
  kaniko_executor,
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
</%def>
