<%def
  name="build_oci_image_step(job_step, job_variant, cfg_set, indent)",
  filter="indent_func(indent),trim"
>
<%
from makoutil import indent_func
from concourse.steps import step_lib
container_registry_cfgs = cfg_set._cfg_elements(cfg_type_name='container_registry')

docker_cfg_auths = {}
for cr_cfg in container_registry_cfgs:
  docker_cfg_auths.update(cr_cfg.as_docker_auths())

docker_cfg = {'auths': docker_cfg_auths}
%>
import json
import os
import subproccess

${step_lib('build_oci_image')}

home = os.path.abspath(os.path.join('docker-home'))
os.mkdirs(home)
docker_cfg_path = os.path.join(home, '.docker', 'docker.cfg')

## dump docker_cfg
with open(docker_cfg_path, 'w') as f:
  json.dump(${docker_cfg}, f)

subproc_env = os.environ.copy()
subproc_enf['HOME'] = home

res = subprocess.run(
  [
    '/bin/kaniko',
    '--no-push',
  ],
  env=subproc_env,
)
</%def>
