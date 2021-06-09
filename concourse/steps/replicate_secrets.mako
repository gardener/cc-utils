<%def name="replicate_secrets_step(step, job, job_mapping, indent)", filter="indent_func(indent),trim">
<%
from makoutil import indent_func
from concourse.steps import step_lib

extra_args = step._extra_args
cfg_dir_path = extra_args['cfg_dir_path']
kubeconfig = extra_args['kubeconfig']
target_secret_namespace = extra_args['target_secret_namespace']

secrets_cfg_name = job_mapping.secret_cfg()
team_name = job_mapping.team_name()
target_secret_name = job_mapping.target_secret_name()

%>

${step_lib('replicate_secrets')}

## use logger from step_lib
logger.info(f'replicating team ${team_name}')

replicate_secrets(
  cfg_dir_env_name='${cfg_dir_path}',
  kubeconfig=dict(${kubeconfig}),
  secrets_cfg_name='${secrets_cfg_name}',
  team_name='${team_name}',
  target_secret_name='${target_secret_name}',
  target_secret_namespace='${target_secret_namespace}',
)

</%def>
