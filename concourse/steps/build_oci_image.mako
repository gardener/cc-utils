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
import ci.log
import oci
import oci.model as om
import oci.util as ou

import shutil

ci.log.configure_default_logging()
logger = logging.getLogger('kaniko-build.step')

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

# XXX ugly hack: early-import so we survive kaniko's rampage (will purge container during build)
import ccc.secrets_server
import model.concourse
import model.container_registry
import model.elasticsearch
import concurrent.futures
import concurrent.futures.thread

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

# XXX final hack (I hope): cp entire python-dir
import sys
import shutil
if sys.version_info.minor >= 9 or sys.version_info.major > 3:
  lib_dir = os.path.join(sys.prefix, sys.platlibdir)
else:
  lib_dir = os.path.join(sys.prefix, 'lib')

# Initialise oci client before kaniko removes _everything_, otherwise cfg-element-retrieval will
# fail
oci_client = ccc.oci.oci_client()

python_lib_dir = os.path.join(lib_dir, f'python{sys.version_info.major}.{sys.version_info.minor}')
python_bak_dir = os.path.join('/', 'kaniko', 'python.bak')
if os.path.isdir(python_lib_dir):
   shutil.copytree(python_lib_dir, python_bak_dir)

# HACK remove '/usr/lib' and '/cc/utils' to avoid pip from failing in the first stage of builds
shutil.rmtree(path=os.path.join('/', 'usr', 'lib'), ignore_errors=True)
shutil.rmtree(path=os.path.join('/', 'cc', 'utils'), ignore_errors=True)

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

print(f'wrote image {image_ref=} to {image_outfile=} attempting to push')

os.makedirs(os.path.dirname(certifi_certs_path), exist_ok=True)
if not os.path.exists(certifi_certs_path):
  os.link(certifi_bak, certifi_certs_path)

os.makedirs(os.path.dirname(ca_certs_path), exist_ok=True)
if not os.path.exists(ca_certs_path):
  os.link(ca_certs_bak, ca_certs_path)

if not os.path.exists(python_lib_dir):
  os.symlink(python_bak_dir, python_lib_dir)

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
