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
import tempfile
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

root = tempfile.TemporaryDirectory().name

home = os.path.join(root, 'kaniko')
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

image_outfile = os.path.join(
  home,
  (outfile_fname := '${image_descriptor.name()}.oci-image.tar'),
)
chroot_image_outfile = os.path.join(
  '/kaniko',
  outfile_fname,
)

with open('${version_path}') as f:
  effective_version = f.read().strip()

image_tag = '${image_descriptor.tag_template()}'.replace(
  '${eff_version_replace_token}',
   effective_version
)

image_ref = f'${image_descriptor.image_reference()}:{image_tag}'

# XXX rm migration-code again
if os.path.exists('/kaniko/executor'):
  kaniko_executor_src = '/kaniko/executor'
else:
  kaniko_executor_src = '/bin/kaniko'

## XXX: workaround "file busy" - use different fname
kaniko_executor_tgt = os.path.join(home, 'executor.cp')

shutil.copyfile(kaniko_executor_src, kaniko_executor_tgt)
shutil.copystat(kaniko_executor_src, kaniko_executor_tgt)

# relative to chroot env
kaniko_executor = '/kaniko/executor.cp'

chroot = shutil.which('chroot')

## cp build-ctx
build_ctx_tgt = os.path.join(root, 'build')
shutil.copytree('${build_ctx_dir}', build_ctx_tgt)

chroot_build_ctx_dir = '/build' # relative to chroot env

## cp dockerfile
dockerfile_tgt = os.path.join(build_ctx_tgt, 'Dockerfile')
shutil.copyfile('${dockerfile_relpath}', dockerfile_tgt)
chroot_dockerfile = os.path.join(chroot_build_ctx_dir, 'Dockerfile')

## xxx cp etc (need to reduce number of files..)
shutil.copytree('/etc', os.path.join(root, 'etc'))

os.makedirs(chroot_dev := os.path.join(root, 'dev'))
os.makedirs(chroot_proc := os.path.join(root, 'proc'))
subprocess.run(('mount', '--bind', '/dev', chroot_dev))
subprocess.run(('mount', '--bind', '/proc', chroot_proc))

kaniko_argv = (
  chroot,
  root,
  kaniko_executor,
  '--no-push',
  '--force',
  '--dockerfile', chroot_dockerfile,
  '--context', chroot_build_ctx_dir,
  '--tarPath', chroot_image_outfile,
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

additional_tags = ${image_descriptor.additional_tags()}
print(f'publishing to {image_ref=}, {additional_tags=}')

manifest_mimetype = om.DOCKER_MANIFEST_SCHEMA_V2_MIME
oci_client = ccc.oci.oci_client()

oci.publish_container_image_from_kaniko_tarfile(
  image_tarfile_path=image_outfile,
  oci_client=oci_client,
  image_reference=image_ref,
  additional_tags=additional_tags,
  manifest_mimetype=manifest_mimetype,
)
</%def>
