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

version_path = os.path.join(job_step.input('version_path'), 'version')

eff_version_replace_token = '${EFFECTIVE_VERSION}'
%>
import json
import os
import subprocess

import container.registry as cr

${step_lib('build_oci_image')}

home = os.path.join('/', 'kaniko')
docker_cfg_dir = os.path.join(home, '.docker')
os.makedirs(docker_cfg_dir, exist_ok=True)
docker_cfg_path = os.path.join(docker_cfg_dir, 'config.json')

## dump docker_cfg
with open(docker_cfg_path, 'w') as f:
  json.dump(${docker_cfg}, f)

subproc_env = os.environ.copy()
subproc_env['HOME'] = home

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

res = subprocess.run(
  [
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
  ],
  env=subproc_env,
  check=True,
)

print(f'wrote image {image_ref=} to {image_outfile=} attempting to push')

fh = open(image_outfile)
cr.publish_container_image(
  image_reference=image_ref,
  image_file_obj=fh,
)
</%def>
