<%namespace file="/resources/defaults.mako" import="*"/>
<%def name="email_notification(
  cfg_set,
  repo_cfgs,
  job_step,
  subject,
  job_variant,
  env_vars,
  inputs,
  indent=0,
  )" filter="indent_func(indent),trim">
<%
import concourse.steps
notification_step = concourse.steps.step_def('notification')
from makoutil import indent_func
%>
- task: '${job_step.name}.failed'
  config:
    inputs:
% for input in inputs:
    - name: ${input}
% endfor
    params:
% for key, value in env_vars.items():
      ${key}: ${value}
% endfor
    ${task_image_defaults(cfg_set.container_registry(), indent=4)}
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
          indent=8
        )}
</%def>
