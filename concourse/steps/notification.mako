<%def
  name="notification_step(
    job_step,
    job_variant,
    cfg_set,
    repo_cfgs,
    subject,
    indent
  )",
  filter="indent_func(indent),trim"
>
<%
from makoutil import indent_func
from concourse.steps import step_lib
# xxx: for now, assume all repositories are from same github
default_github_cfg_name = cfg_set.github().name()
cc_email_cfg = cfg_set.email()

notification_cfg = job_step.notifications_cfg()
notification_cfg_name = notification_cfg.name()
on_error_cfg = notification_cfg.on_error()
triggering_policy = on_error_cfg.triggering_policy()
on_error_dir = job_step.output('on_error_dir')

if job_variant.has_trait('component_descriptor'):
  component_name = job_variant.trait('component_descriptor').component_name()
else:
  component_name = None # todo: fallback to main repository
%>
import sys
import os
import traceback

import github
import util
import mailutil


from util import ctx
cfg_factory = ctx().cfg_factory()
cfg_set = cfg_factory.cfg_set("${cfg_set.name()}")

${step_lib('notification')}

meta_vars_dict = meta_vars()
concourse_api = from_cfg(cfg_set.concourse(), team_name=meta_vars_dict['build-team-name'])
## TODO: Replace with MAIN_REPO_DIR once it is available in synthetic steps
path_to_main_repository = "${job_variant.main_repository().resource_name()}"

util.info('Notification cfg: ${notification_cfg_name}')
util.info('Triggering policy: ${triggering_policy}')
util.info("Will notify: ${on_error_cfg.recipients()}")

if not should_notify(
    NotificationTriggeringPolicy('${triggering_policy.value}'),
    meta_vars=meta_vars_dict,
):
    print('will not notify due to policy')
    sys.exit(0)

## prepare notification config.
notify_file = os.path.join('${on_error_dir}', 'notify.cfg')
email_cfg = {
  'recipients': set(),
  'component_names': set(),
  'mail_body': None,
  'codeowners_files': set(),
}
if os.path.isfile(notify_file):
## custom notification config found in error dir
    notify_cfg = util.parse_yaml_file(notify_file)
    email_cfg.update(notify_cfg.get('email', dict()))
    ## Convert elements of notify config to sets
    email_cfg['component_names'] = set(email_cfg.get('component_names', set()))
    email_cfg['recipients'] = set(email_cfg.get('recipients', set()))
    email_cfg['codeowner_files'] = set(email_cfg.get('codeowner_files', set()))
    util.info(f'found notify.cfg - applying cfg: \n{notify_cfg}')

notify_cfg = {'email': email_cfg}

main_repo_github_cfg = cfg_set.github("${job_variant.main_repository().cfg_name() or default_github_cfg_name}")
main_repo_github_api = github.util._create_github_api_object(main_repo_github_cfg)

if 'component_diff_owners' in ${on_error_cfg.recipients()}:
    component_diff_path = os.path.join('component_descriptor_dir', 'dependencies.diff')
    util.info('adding mail recipients from component diff since last release')
    components = components_with_version_changes(component_diff_path)
    ## Recipient-address resolution from component names will be done at a later point
    email_cfg['component_names'] = email_cfg.get('component_names', set()) | set(components)

if 'codeowners' in ${on_error_cfg.recipients()}:
    ## Add codeowners from main repository to recipients
    util.info('adding codeowners from main repository as recipients')
    recipients = set(
        mailutil.determine_local_repository_codeowners_recipients(
            github_api=main_repo_github_api,
            src_dirs=(path_to_main_repository,),
            )
        )
    email_cfg['recipients'] = email_cfg.get('recipients', set()) | recipients

## Also consider explicitly given CODEOWNERS files
if email_cfg['codeowners_files']:
    util.info("adding codeowners from explicitly configured 'CODEOWNERS' files")
    recipients = set(
        mailutil.determine_codeowner_file_recipients(
            github_api=main_repo_github_api,
            codeowners_files=email_cfg['codeowners_files'],
        )
    )
    email_cfg['recipients'] = email_cfg.get('recipients', set()) | recipients

if 'email_addresses' in ${on_error_cfg.recipients()}:
    util.info('adding excplicitly configured recipients')
    recipients = set(${on_error_cfg.recipients().get('email_addresses',())})
    email_cfg['recipients'] = email_cfg.get('recipients', set()) | recipients

if 'committers' in ${on_error_cfg.recipients()}:
    util.info('adding committers of main repository to recipients')
    recipients = set(mailutil.determine_head_commit_recipients(
            src_dirs=(path_to_main_repository,),
        ))
    email_cfg['recipients'] = email_cfg.get('recipients', set()) | recipients

def default_mail_recipients():
    recipients = set()
% for repo_cfg in repo_cfgs:
## Get default (i.e. committer of head commit and codeowners) from local repositories
    recipients.update(mailutil.determine_mail_recipients(
        github_cfg_name="${repo_cfg.cfg_name() or default_github_cfg_name}",
        src_dirs=("${repo_cfg.resource_name()}",),
    ))
    return recipients
% endfor

## Fill notify_cfg with default values if none configured
if not email_cfg.get('recipients'):
    email_cfg['recipients'] = default_mail_recipients()
if not email_cfg.get('mail_body'):
    email_cfg['mail_body'] = retrieve_build_log(
        concourse_api=concourse_api,
        task_name='${job_step.name}',
    )

## Finally, determine recipients for all component names gathered
recipients = resolve_recipients_by_component_name(
    component_names=email_cfg.get('component_names', ()),
    github_cfg_name="${default_github_cfg_name}",
)
email_cfg['recipients'] = email_cfg['recipients'] | set(recipients)

## Send mail
email_cfg_name = "${cc_email_cfg.name()}"
mailutil.notify(
    subject="${subject}",
    body='\n'.join((job_url(meta_vars_dict), email_cfg['mail_body'])),
    email_cfg_name=email_cfg_name,
    recipients=email_cfg['recipients'],
)
</%def>
