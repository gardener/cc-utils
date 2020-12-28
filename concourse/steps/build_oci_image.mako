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
  image_descriptor.dockerfile_relpath()
)
build_ctx_dir = os.path.join(
  job_step.input('image_path'),
  image_descriptor.builddir_relpath() or '',
)

docker_cfg_auths = {}
for cr_cfg in container_registry_cfgs:
  docker_cfg_auths.update(cr_cfg.as_docker_auths())

docker_cfg = {'auths': docker_cfg_auths}
%>
import json
import os
import subprocess

${step_lib('build_oci_image')}

home = os.path.abspath(os.path.join('docker-home'))
docker_cfg_dir = os.path.join(home, '.docker')
os.makedirs(docker_cfg_dir)
docker_cfg_path = os.path.join(docker_cfg_dir, 'config.json')

## dump docker_cfg
with open(docker_cfg_path, 'w') as f:
  json.dump(${docker_cfg}, f)

subproc_env = os.environ.copy()
subproc_env['HOME'] = home

res = subprocess.run(
  [
    '/bin/kaniko',
    '--no-push',
    '--dockerfile', '${dockerfile_relpath}',
    '--context', '${build_ctx_dir}',
  ],
  env=subproc_env,
)
</%def>
