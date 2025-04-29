<%def name="replicate_secrets_step(step, job, indent)", filter="indent_func(indent),trim">
<%
from makoutil import indent_func
from concourse.steps import step_lib

extra_args = step._extra_args
concourse_target_team_name = extra_args['concourse_target_team_name']
cfg_repo_relpath = extra_args['cfg_repo_relpath']
config_repo_org = extra_args['config_repo_org']
config_repo_repo = extra_args['config_repo_repo']
config_repo_url = extra_args['config_repo_url']
config_repo_github_cfg = extra_args['config_repo_github_cfg']
config_repo_url = extra_args['config_repo_url']

do_rotate_secrets = bool(extra_args.get('rotate_secrets', False))
%>

${step_lib('replicate_secrets')}

import ccc.github
import cfg_mgmt.reporting as cmr
import model
import model.concourse
import model.config_repo

cfg_dir = '${cfg_repo_relpath}'

config_repo_org = '${config_repo_org}'
config_repo_repo = '${config_repo_repo}'
config_repo_github_cfg ='${config_repo_github_cfg}'
config_repo_url = '${config_repo_url}'


% if do_rotate_secrets:
try:
    cfg_factory: model.ConfigFactory = model.ConfigFactory.from_cfg_dir(cfg_dir=cfg_dir)
    github_cfg = ccc.github.github_cfg_for_repo_url(
        repo_url=config_repo_url,
        cfg_factory=cfg_factory,
    )
    github_api = ccc.github.github_api(
        github_cfg=github_cfg,
        cfg_factory=cfg_factory,
    )
    config_repo = github_api.repository(config_repo_org, config_repo_repo)
    config_repo_default_branch = config_repo.default_branch

    rotate_secrets(
        cfg_dir=cfg_dir,
        target_ref=f'refs/heads/{config_repo_default_branch}',
        repo_url=config_repo_url,
        github_repo_path=f'{config_repo_org}/{config_repo_repo}',
    )
except:
    ## we are paranoid: let us not break replication upon rotation-error for now
    import traceback
    traceback.print_exc()
% else:
logger.info('will not rotate secrets (disabled for this pipeline)')
% endif

team_name = '${concourse_target_team_name}'

logger.info(f'using repo in {cfg_dir}')
cfg_factory: model.ConfigFactory = model.ConfigFactory.from_cfg_dir(
    cfg_dir=cfg_dir,
)
replication_target_config = model.config_repo.replication_config_from_cfg_dir(cfg_dir)

## use logger from step_lib
logger.info(f'replicating team {team_name}')

replicate_secrets(
    cfg_factory=cfg_factory,
    replication_target_config=replication_target_config,
)

logger.info('generating cfg element status report')

status_reports = cmr.generate_cfg_element_status_reports(
    cfg_dir=cfg_dir,
    element_storage=config_repo_url,
)
cmr.create_report(status_reports)

% if do_rotate_secrets:
try:
    process_config_queue(
        cfg_dir=cfg_dir,
        target_ref=f'refs/heads/{config_repo_default_branch}',
        repo_url=config_repo_url,
        github_repo_path=f'{config_repo_org}/{config_repo_repo}',
    )
except:
    import traceback
    traceback.print_exc()
% endif

</%def>
