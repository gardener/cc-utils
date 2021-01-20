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
main_repo_hostname = job_variant.main_repository().repo_hostname()
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
if (component_descriptor_trait := job_variant.trait('component_descriptor', None)):
  ctx_repo_url = component_descriptor_trait.ctx_repository_base_url()
else:
  # fallback to default ctx-repo
  ctx_repo_url = cfg_set.ctx_repository().base_url()
%>
import sys
import os

import ccc.github
import cnudie.retrieve
import ci.util
import github
import mailutil
import product.v2


from ci.util import ctx

CC_ROOT_DIR = os.path.abspath('.')
os.environ['CC_ROOT_DIR'] = CC_ROOT_DIR
cfg_factory = ctx().cfg_factory()
cfg_set = cfg_factory.cfg_set("${cfg_set.name()}")

${step_lib('notification')}

meta_vars_dict = meta_vars()
env_build_job_name = os.environ.get('BUILD_JOB_NAME').strip()
meta_build_job_name = meta_vars_dict.get('build-job-name').strip()
meta_resource_inconsistent = False
if meta_build_job_name != env_build_job_name:
    meta_resource_inconsistent = True
    ci.util.warning(
        'Inconsistent META resource. Job URL in email cannot be determined\n'
        f'Expected job name: {env_build_job_name}\n'
        f'Job name in META resource: {meta_build_job_name}'
    )

concourse_api = from_cfg(cfg_set.concourse(), team_name=meta_vars_dict['build-team-name'])
## TODO: Replace with MAIN_REPO_DIR once it is available in synthetic steps
path_to_main_repository = "${job_variant.main_repository().resource_name()}"

ci.util.info('Notification cfg: ${notification_cfg_name}')
ci.util.info('Triggering policy: ${triggering_policy}')
ci.util.info("Will notify: ${on_error_cfg.recipients()}")

if not should_notify(
    NotificationTriggeringPolicy('${triggering_policy.value}'),
    meta_vars=meta_vars_dict,
    cfg_set=cfg_set,
):
    print('will not notify due to policy')
    sys.exit(0)

## prepare notification config.
notify_file = os.path.join('${on_error_dir}', 'notify.cfg')
email_cfg = {
  'recipients': set(),
  'component_name_recipients': set(),
  'mail_body': None,
  'subject': None,
  'codeowners_files': set(),
}
if os.path.isfile(notify_file):
## custom notification config found in error dir
    notify_cfg = ci.util.parse_yaml_file(notify_file)
    email_cfg.update(notify_cfg.get('email', dict()))
    ## Convert elements of notify config to sets
    email_cfg['component_name_recipients'] = set(email_cfg.get('component_name_recipients', set()))
    email_cfg['recipients'] = set(email_cfg.get('recipients', set()))
    email_cfg['codeowner_files'] = set(email_cfg.get('codeowner_files', set()))
    ci.util.info(f'found notify.cfg - applying cfg: \n{notify_cfg}')

notify_cfg = {'email': email_cfg}

email_cfg['subject'] = email_cfg['subject'] or '${subject}'

main_repo_github_cfg = ccc.github.github_cfg_for_hostname('${main_repo_hostname}')
main_repo_github_api = ccc.github.github_api(main_repo_github_cfg)

if 'component_diff_owners' in ${on_error_cfg.recipients()}:
    component_diff_path = os.path.join('component_descriptor_dir', 'dependencies.diff')
    ci.util.info('adding mail recipients from component diff since last release')
    components = components_with_version_changes(component_diff_path)
    ## Recipient-address resolution from component names will be done at a later point
    email_cfg['component_name_recipients'] = email_cfg.get('component_name_recipients', set()) | set(components)

if 'codeowners' in ${on_error_cfg.recipients()}:
    ## Add codeowners from main repository to recipients
    ci.util.info('adding codeowners from main repository as recipients')
    recipients = set(
        mailutil.determine_local_repository_codeowners_recipients(
            github_api=main_repo_github_api,
            src_dirs=(path_to_main_repository,),
            )
        )
    email_cfg['recipients'] = email_cfg.get('recipients', set()) | recipients

## Also consider explicitly given CODEOWNERS files
if email_cfg['codeowners_files']:
    ci.util.info("adding codeowners from explicitly configured 'CODEOWNERS' files")
    recipients = set(
        mailutil.determine_codeowner_file_recipients(
            github_api=main_repo_github_api,
            codeowners_files=email_cfg['codeowners_files'],
        )
    )
    email_cfg['recipients'] = email_cfg.get('recipients', set()) | recipients

if 'email_addresses' in ${on_error_cfg.recipients()}:
    ci.util.info('adding excplicitly configured recipients')
    recipients = set(${on_error_cfg.recipients().get('email_addresses',())})
    email_cfg['recipients'] = email_cfg.get('recipients', set()) | recipients

if 'committers' in ${on_error_cfg.recipients()}:
    ci.util.info('adding committers of main repository to recipients')
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
def retr_component(component_name: str):
  greatest_version = product.v2.greatest_component_version(
    component_name=component_name,
    ctx_repo_base_url='${ctx_repo_url}',
  )
  comp_descr = cnudie.retrieve.component_descriptor(
    name=component_name,
    version=greatest_version,
    ctx_repo_url='${ctx_repo_url}',
  )
  return comp_descr.component


components = [
  retr_component(component_name=cname) for cname in email_cfg.get('component_name_recipients', ())
]

recipients = resolve_recipients_by_component_name(
    components=email_cfg.get('component_name_recipients', ()),
    github_cfg_name="${default_github_cfg_name}",
)
email_cfg['recipients'] = email_cfg['recipients'] | set(recipients)

## Send mail
email_cfg_name = "${cc_email_cfg.name()}"
if meta_resource_inconsistent:
    body = '\n'.join(
        (f'The Job URL cannot be determined. Please check your job "{env_build_job_name}"',
        f'in pipeline "{meta_vars_dict["build-pipeline-name"]}"','',
        email_cfg['mail_body'],)
    )
else:
    body = '\n'.join((job_url(meta_vars_dict), email_cfg['mail_body']))
mailutil.notify(
    subject=email_cfg['subject'],
    body=body,
    email_cfg_name=email_cfg_name,
    recipients=email_cfg['recipients'],
)
</%def>
