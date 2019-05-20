<%namespace file="/resources/defaults.mako" import="*"/>
<%def name="email_notification(
  cfg_set,
  secrets_server_cfg,
  email_cfg,
  repo_cfgs,
  job_step,
  subject,
  job_variant,
  as_list=False,
  indent=0
  )" filter="indent_func(indent),trim">
<%
import concourse.steps
notification_step = concourse.steps.step_def('notification')
from makoutil import indent_func
repo_cfgs = list(repo_cfgs)
src_dirs = [repo_cfg.resource_name() for repo_cfg in repo_cfgs]
# xxx: for now, assume all repositories are from same github
default_github_cfg_name = cfg_set.github().name()

notification_cfg = job_step.notifications_cfg()
on_error_cfg = notification_cfg.on_error()
%>
% if as_list:
- do:
% else:
  do:
% endif
  - task: '${job_step.name}_failed'
    config:
      inputs:
% for src_dir in src_dirs:
      - name: ${src_dir}
% endfor
% for input in on_error_cfg.inputs():
      - name: ${input}
% endfor
      - name: ${job_variant.meta_resource_name()}
      params:
        SECRETS_SERVER_ENDPOINT: ${secrets_server_cfg.endpoint_url()}
        SECRETS_SERVER_CONCOURSE_CFG_NAME: ${secrets_server_cfg.secrets().concourse_cfg_name()}
        BUILD_JOB_NAME: ${job_variant.job_name()}
        META: ${job_variant.meta_resource_name()}
      ${task_image_defaults(cfg_set.container_registry(), indent=6)}
      run:
        path: /usr/bin/python3
        args:
        - -c
        - |
          ${notification_step(
            job_step=job_step,
            job_variant=job_variant,
            cfg_set=cfg_set,
            repo_cfgs=repo_cfgs,
            subject=subject,
            indent=10
          )}
</%def>
