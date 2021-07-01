<%def name="replicate_secrets_step(step, job, job_mapping, indent)", filter="indent_func(indent),trim">
<%
from makoutil import indent_func
from concourse.steps import step_lib

extra_args = step._extra_args
cfg_repo_relpath = extra_args['cfg_repo_relpath']
kubeconfig = extra_args['kubeconfig']
target_secret_namespace = extra_args['target_secret_namespace']
raw_secret_cfg = extra_args['secret_cfg']
raw_job_mapping = extra_args['raw_job_mapping']
job_mapping_name = extra_args['job_mapping_name']

%>

${step_lib('replicate_secrets')}

import model
import model.concourse

org_job_mapping = model.concourse.JobMapping(name='${job_mapping_name}', raw_dict=${raw_job_mapping})
team_name = org_job_mapping.team_name()

logger.info('using repo in ${cfg_repo_relpath}')
cfg_factory: model.ConfigFactory = model.ConfigFactory.from_cfg_dir(
  cfg_dir='${cfg_repo_relpath}',
)
cfg_set = cfg_factory.cfg_set(org_job_mapping.replication_ctx_cfg_set())

## use logger from step_lib
logger.info(f'replicating team {team_name}')

raw_secret_cfg = ${raw_secret_cfg}

replicate_secrets(
  cfg_factory=cfg_factory,
  cfg_set=cfg_set,
  kubeconfig=dict(${kubeconfig}),
  secret_key=raw_secret_cfg.get('key'),
  secret_cipher_algorithm=raw_secret_cfg.get('cipher_algorithm'),
  team_name=team_name,
  target_secret_name=org_job_mapping.target_secret_name(),
  target_secret_namespace='${target_secret_namespace}',
  target_secret_cfg_name=org_job_mapping.target_secret_cfg_name(),
)

</%def>
