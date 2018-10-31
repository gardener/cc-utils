<%def
  name="notification_step(
    job_step,
    cfg_set,
    repo_cfgs,
    subject,
    indent
  )",
  filter="indent_func(indent),trim"
>
<%
from makoutil import indent_func
from concourse.model.step import NotificationPolicy
# xxx: for now, assume all repositories are from same github
default_github_cfg_name = cfg_set.github().name()
email_cfg = cfg_set.email()

notification_cfg = job_step.notification_cfg()
on_error_policy = notification_cfg.on_error_policy()
on_error_dir = job_step.output('on_error_dir')
%>
import sys
import os
import traceback

import util
import mailutil

v = {}
for name in [
  'build-id',
  'build-name',
  'build-job-name',
  'build-team-name',
  'build-pipeline-name',
  'atc-external-url'
  ]:
  with open(os.path.join('meta', name)) as f:
    v[name] = f.read().strip()

job_url = '/'.join([
  v['atc-external-url'],
  'teams',
  v['build-team-name'],
  'pipelines',
  v['build-pipeline-name'],
  'jobs',
  v['build-job-name'],
  'builds',
  v['build-name']
])

from util import ctx
cfg_factory = ctx().cfg_factory()
cfg_set = cfg_factory.cfg_set("${cfg_set.name()}")

# determine previous build
from concourse.client import from_cfg, BuildStatus
concourse_api = from_cfg(cfg_set.concourse(), team_name=v['build-team-name'])

print('Error notification policy: ${on_error_policy.name}')

try:
  build_number = int(v['build-name'])
  previous_build = str(build_number - 1)
  previous_build = concourse_api.job_build(
    pipeline_name=v['build-pipeline-name'],
    job_name=v['build-job-name'],
    build_name=previous_build
  )
  # assumption: current job failed
  if previous_build.status() in (BuildStatus.FAILED, BuildStatus.ERRORED):
    print('previous build was already broken - will not notify')
    sys.exit(0)
except Exception as e:
  if type(e) == SystemExit:
    raise e
  # in doubt, ensure notification is sent
  traceback.print_exc()

def retrieve_build_log():
    try:
      build_id = v['build-id']
      task_id = concourse_api.build_plan(build_id=build_id).task_id(task_name='${job_step.name}')
      build_events = concourse_api.build_events(build_id=build_id)
      build_log = '\n'.join(build_events.iter_buildlog(task_id=task_id))
      return build_log
    except Exception as e:
      traceback.print_exc() # print_err, but send email notification anyway
      return 'failed to retrieve build log'

notify_file = os.path.join('${on_error_dir}', 'notify.cfg')
if os.path.isfile(notify_file):
  notify_cfg = util.parse_yaml_file(notify_file)
  email_cfg = notify_cfg.get('email', {})
  util.info('found notify.cfg - applying cfg:')
  print(notify_cfg)
else:
  email_cfg = {
    'recipients': None,
    'mail_body': None,
  }
  notify_cfg = {'email': email_cfg}

def default_mail_recipients():
  recipients = set()
% for repo_cfg in repo_cfgs:
  recipients.update(mailutil.determine_mail_recipients(
    github_cfg_name="${repo_cfg.cfg_name() if repo_cfg.cfg_name() else default_github_cfg_name}",
    src_dirs=("${repo_cfg.resource_name()}",),
    )
  )
  return recipients
% endfor

# fill notify_cfg with default values if not configured
if not email_cfg.get('recipients'):
  email_cfg['recipients'] = default_mail_recipients()
if not email_cfg.get('mail_body'):
  email_cfg['mail_body'] = retrieve_build_log()


# determine mail recipients
email_cfg_name = "${email_cfg.name()}"
mailutil.notify(
  subject="${subject}",
  body='\n'.join((job_url, email_cfg['mail_body'])),
  email_cfg_name=email_cfg_name,
  recipients=email_cfg['recipients'],
)
</%def>
