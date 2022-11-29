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
secrets_repo_url = extra_args['secrets_repo_url']
cfg_repo_url = extra_args['cfg_repo_url']
do_rotate_secrets = bool(extra_args.get('rotate_secrets', False))
%>

${step_lib('replicate_secrets')}

import ccc.elasticsearch
import ccc.github
import cfg_mgmt.reporting as cmr
import model
import model.concourse

cfg_dir = '${cfg_repo_relpath}'

secrets_repo_dict = ${raw_job_mapping['secrets_repo']}
secrets_repo_org = secrets_repo_dict['org']
secrets_repo_repo = secrets_repo_dict['repo']
secrets_repo_url = '${secrets_repo_url}'


% if do_rotate_secrets:
try:
  cfg_factory: model.ConfigFactory = model.ConfigFactory.from_cfg_dir(cfg_dir=cfg_dir)
  if secrets_repo_url:
    github_cfg = ccc.github.github_cfg_for_repo_url(
      repo_url=secrets_repo_url,
      cfg_factory=cfg_factory,
    )
  else:
    ## TODO: remove else-case after release of cc-utils >= 1.1581.0
    logger.warning('no secrets_repo_url - falling back to github_cfg name')
    github_cfg = cfg_factory.github(secrets_repo_dict['github_cfg'])

  github_api = ccc.github.github_api(
    github_cfg=github_cfg,
    cfg_factory=cfg_factory,
  )
  secrets_repo = github_api.repository(secrets_repo_org, secrets_repo_repo)
  secrets_repo_default_branch = secrets_repo.default_branch

  ## TODO: remove if after release of cc-utils >= 1.1581.0
  if not secrets_repo_url:
    secrets_repo_url = f'{github_cfg.ssh_url()}/{secrets_repo_org}/{secrets_repo_repo}'

  rotate_secrets(
    cfg_dir=cfg_dir,
    target_ref=f'refs/heads/{secrets_repo_default_branch}',
    repo_url=secrets_repo_url,
    github_repo_path=f'{secrets_repo_org}/{secrets_repo_repo}',
  )
except:
  ## we are paranoid: let us not break replication upon rotation-error for now
  import traceback
  traceback.print_exc()
% else:
logger.info('will not rotate secrets (disabled for this pipeline)')
% endif

org_job_mapping = model.concourse.JobMapping(name='${job_mapping_name}', raw_dict=${raw_job_mapping})
team_name = org_job_mapping.team_name()

logger.info('using repo in ${cfg_repo_relpath}')
cfg_factory: model.ConfigFactory = model.ConfigFactory.from_cfg_dir(
  cfg_dir=cfg_dir,
)
cfg_set = cfg_factory.cfg_set(org_job_mapping.replication_ctx_cfg_set())

## use logger from step_lib
logger.info(f'replicating team {team_name}')

raw_secret_cfg = ${raw_secret_cfg}
future_secrets =  {k:v for (k,v) in raw_secret_cfg.items() if k.startswith('key-')}

replicate_secrets(
  cfg_factory=cfg_factory,
  cfg_set=cfg_set,
  kubeconfig=dict(${kubeconfig}),
  secret_key=raw_secret_cfg.get('key'),
  future_secrets=future_secrets,
  secret_cipher_algorithm=raw_secret_cfg.get('cipher_algorithm'),
  team_name=team_name,
  target_secret_name=org_job_mapping.target_secret_name(),
  target_secret_namespace='${target_secret_namespace}',
  target_secret_cfg_name=org_job_mapping.target_secret_cfg_name(),
)

logger.info('generating cfg element status report')

status_reports = cmr.generate_cfg_element_status_reports(
  cfg_dir='${cfg_repo_relpath}',
  element_storage='${cfg_repo_url}',
)
cmr.create_report(status_reports)
cfg_report_summary_gen = cmr.cfg_element_statuses_storage_summaries(status_reports)
cfg_responsible_summary_gen = cmr.cfg_element_statuses_responsible_summaries(status_reports)

if (es_client := ccc.elasticsearch.from_cfg(cfg_set.elasticsearch())):
    logger.info('writing cfg metrics to elasticsearch')
    cmr.cfg_compliance_status_to_es(
        es_client=es_client,
        cfg_report_summary_gen=cfg_report_summary_gen,
    )
    cmr.cfg_compliance_responsibles_to_es(
        es_client=es_client,
        cfg_element_statuses=status_reports,
    )
    cmr.cfg_compliance_storage_responsibles_to_es(
        es_client=es_client,
        cfg_responsible_summary_gen=cfg_responsible_summary_gen,
    )
else:
    logger.warning('not writing cfg status to elasticsearch, no client available')

% if do_rotate_secrets:
process_config_queue(
  cfg_dir=cfg_dir,
  target_ref=f'refs/heads/{secrets_repo_default_branch}',
  repo_url=secrets_repo_url,
  github_repo_path=f'{secrets_repo_org}/{secrets_repo_repo}',
)
% endif

</%def>
