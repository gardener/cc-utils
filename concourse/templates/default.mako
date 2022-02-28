---
<%
import os

import oci.auth as oa
import model.container_registry
import model.secrets_server

from ci.util import urljoin
from makoutil import indent_func
from concourse.model.base import ScriptType
from concourse.model.step import StepNotificationPolicy, PrivilegeMode
from concourse.model.traits.component_descriptor import DEFAULT_COMPONENT_DESCRIPTOR_STEP_NAME
from concourse.model.traits.publish import OciBuilder

# use pipeline_name for debugging / tracing purposes
pipeline_name = pipeline.get('name')
pipeline_definition = pipeline.get('definition')
target_team = pipeline.get('target_team')
background_image = pipeline.get('background_image', 'https://i.imgur.com/raPlg21.png')
job_mapping = pipeline.get('job_mapping')
secret_cfg = pipeline.get('secret_cfg')

# fetch metadata and prepare it for the meta-step
meta_data_dict = {
  'render origin': pipeline.get('render_origin'),
  'cc-utils version used for rendering': pipeline.get('cc_utils_version'),
}
if replication_pipeline_name := pipeline.get('replication_pipeline_name'):
  meta_data_dict['replication pipeline name'] = replication_pipeline_name
if pipeline_definition_committish := pipeline.get('pipeline_definition_committish'):
  meta_data_dict['pipeline definition committish'] = pipeline_definition_committish

resource_registry = pipeline_definition._resource_registry

github = config_set.github()

default_container_registry = config_set.container_registry()

# expose secrets_server endpoint to all jobs
secrets_server_cfg = config_set.secrets_server()

secrets_server_cfg_url_path = model.secrets_server.secret_url_path(
  job_mapping,
  secret_cfg,
)

# short-cut for now
def has_version_trait(model_with_traits):
  return model_with_traits.has_trait('version')

def has_cron_trait(model_with_traits):
  return model_with_traits.has_trait('cronjob')

def has_pr_trait(model_with_traits):
  return model_with_traits.has_trait('pull-request')

def has_release_trait(model_with_traits):
  return model_with_traits.has_trait('release')

def has_publish_trait(model_with_traits):
  return model_with_traits.has_trait('publish')

def has_component_descriptor_trait(model_with_traits):
  return model_with_traits.has_trait('component_descriptor')

def has_options_trait(model_with_traits):
  return model_with_traits.has_trait('options')

def suppress_parallel_execution(variant):
  if variant.has_trait('scheduling'):
    if variant.trait('scheduling').suppress_parallel_execution() is not None:
      return variant.trait('scheduling').suppress_parallel_execution()
  if has_cron_trait(variant):
    return True
  if has_release_trait(variant):
    return True
  return False

# import build steps from cc-utils
# TODO: make this generic
import concourse.steps
version_step = concourse.steps.step_def('version')
prepare_step = concourse.steps.step_def('prepare')
publish_step = concourse.steps.step_def('publish')
release_step = concourse.steps.step_def('release')
build_oci_image_step = concourse.steps.step_def('build_oci_image')
meta_step = concourse.steps.step_def('meta')
rm_pr_label_step = concourse.steps.step_def('rm_pr_label')
replicate_pipelines_step = concourse.steps.step_def('replicate_pipelines')
component_descriptor_step = concourse.steps.step_def('component_descriptor')
update_component_deps_step = concourse.steps.step_def('update_component_deps')
draft_release_step = concourse.steps.step_def('draft_release')
scan_container_images_step = concourse.steps.step_def('scan_container_images')
malware_scan_step = concourse.steps.step_def('malware_scan')
os_id_step = concourse.steps.step_def('os_id')
scan_sources_step = concourse.steps.step_def('scan_sources')
replicate_secrets_step = concourse.steps.step_def('replicate_secrets')
%>

<%namespace file="/resources/defaults.mako" import="*"/>
<%namespace file="/resources/resource_types.mako" import="*"/>
<%namespace file="/resources/email.mako" import="*"/>
<%namespace file="/resources/image.mako" import="*"/>
<%namespace file="/resources/variants.mako" import="*"/>
<%namespace file="/resources/time.mako" import="*"/>

inherit:
${configure_webhook(webhook_token=github.webhook_secret())}
resource_types:
${include_pull_request_resource_type()}

% if len(resource_registry) > 0:
resources:
${render_repositories(pipeline_definition=pipeline_definition, cfg_set=config_set)}

% for variant in pipeline_definition.variants():
  % if has_cron_trait(variant):
<%
trait = variant.trait('cronjob')
interval = trait.interval()
resource_name = trait.resource_name()
%>
${time_resource(name=resource_name, interval=interval)}
  % endif
% endfor
% endif

<%def name="update_pr_status(indent, job_variant, job_step, status)" filter="indent_func(indent),trim">
<%
repo = job_variant.main_repository()
%>
- put: ${repo.resource_name()}
  params:
    path: ${repo.resource_name()}
    context: ${job_step.name}
    status: ${status}
</%def>

<%def name="notification(indent, job_variant, job_step, status)" filter="indent_func(indent),trim">
<%
notify_pull_request = (
  has_pr_trait(job_variant)
  and job_step.notification_policy() is StepNotificationPolicy.NOTIFY_PULL_REQUESTS
)
send_email_notification = not has_pr_trait(job_variant) and status == 'error'
%>
% if notify_pull_request or send_email_notification:
on_failure:
  do:
% endif
% if notify_pull_request:
  ${update_pr_status(2, job_variant, job_step, status)}
% endif
## let's send an e-mail only in case of a problem
## (sucks in comparison to the features the Jenkins extened e-mail plugin offers)
% if send_email_notification:
<%
import concourse.model.traits.meta
if job_variant.has_main_repository():
  repo = job_variant.main_repository()
  branch = repo.branch()
else:
  branch = 'n/a'

subject = 'Step {s} for {p}:{b} failed!'.format(
  s=job_step.name,
   p=pipeline.get('name'),
   b=branch,
)
def repos():
  if job_variant.has_main_repository():
    yield job_variant.main_repository()
    if job_variant.has_publish_repository(job_variant.main_repository().logical_name()):
      yield job_variant.publish_repository(job_variant.main_repository().logical_name())

repo_cfgs = list(repos())
src_dirs = [repo_cfg.resource_name() for repo_cfg in repo_cfgs]
notification_cfg = job_step.notifications_cfg()
on_error_cfg = notification_cfg.on_error()

notification_inputs = [src_dir for src_dir in src_dirs]
notification_inputs.extend([input for input in on_error_cfg.inputs()])
notification_inputs.append(concourse.model.traits.meta.DIR_NAME)

notification_env_vars = {
  concourse.model.traits.meta.ENV_VAR_NAME: concourse.model.traits.meta.DIR_NAME,
  'BUILD_JOB_NAME': job_variant.job_name(),
  'CONCOURSE_CURRENT_CFG': config_set.name(),
  'CONCOURSE_CURRENT_TEAM': target_team,
  'PIPELINE_NAME': pipeline_name,
  'SECRETS_SERVER_CONCOURSE_CFG_NAME': secrets_server_cfg_url_path,
  'SECRETS_SERVER_ENDPOINT': secrets_server_cfg.endpoint_url(),
}

if secret_cfg:
  notification_env_vars['SECRET_CIPHER_ALGORITHM'] = secret_cfg.cipher_algorithm().value
  notification_env_vars['SECRET_KEY'] = secret_cfg.key().decode('utf-8')
  if secret_cfg.generation():
    notification_env_vars['SECRETS_SERVER_GENERATION'] = secret_cfg.generation()

%>
  ${email_notification(
    cfg_set=config_set,
    repo_cfgs=repo_cfgs,
    job_step=job_step,
    subject=subject,
    job_variant=job_variant,
    env_vars=notification_env_vars,
    inputs=notification_inputs,
    indent=2,
  )}
% endif
</%def>

<%def name="execute(indent, job_step, job_variant)" filter="indent_func(indent),trim">
<%
if job_variant.has_main_repository():
  source_repo = job_variant.main_repository()
  source_repo_github_cfg_name = source_repo.cfg_name() or github.name()
else:
  source_repo = None
  source_repo_github_cfg_name = github.name()
%>
% if job_step.execute():
- task: '${job_step.name}'
  privileged: ${'true' if job_step.privilege_mode() is PrivilegeMode.PRIVILEGED else 'false'}
% if job_step.timeout():
  timeout: '${job_step.timeout()}'
% endif
% if job_step.retries():
  attempts: ${job_step.retries()}
% endif
  config:
% if job_step.image():
<%
image_reference, tag = job_step.image().split(':', 1)
if job_step.registry():
  registry_cfg = config_set.container_registry(job_step.registry())
else:
  ## No containerregistry configured. Attempt to find a matching one on our side by looking
  ## at the configured prefixes of the container-registries.
  registry_cfg = model.container_registry.find_config(
    image_reference=image_reference,
    privileges=oa.Privileges.READONLY,
  )
%>
    ${task_image_resource(
        registry_cfg=registry_cfg,
        image_repository=image_reference,
        image_tag=tag,
        indent=4,
    )}
% else:
    ${task_image_defaults(registry_cfg=default_container_registry, indent=4)}
% endif
    inputs:
% for repository in job_variant.repositories():
    - name: ${repository.resource_name()}
% endfor
% for input in job_step.inputs().values():
    - name: ${input}
% endfor
    outputs:
% if job_step.publish_repository_names() and not has_pr_trait(job_variant):
  % for publish_repo_name in job_step.publish_repository_names():
    - name: ${job_variant.publish_repository(publish_repo_name).resource_name()}
  % endfor
% endif
% for output in job_step.outputs().values():
    - name: ${output}
% endfor
% if (cache_paths := job_step.cache_paths()):
    caches:
% for cache_path in cache_paths:
      - path: "${cache_path}"
% endfor
% endif
    params:
<%
# collect repositores that need to be cloned
clone_repositories = [] # [<from:to>, ..]
# name of the cloned main repository
cloned_main_repo_name = None
%>
% for repository in job_variant.repositories():
<%
# the path to map to is usually the repository's resource name
# except for cases where the repository shall be written to; in this
# case we clone the source repository for our users and point them to the
# cloned repository
if job_variant.has_publish_repository(repository.logical_name()) and repository.logical_name() in job_step.publish_repository_names():
  env_var_repo = job_variant.publish_repository(repository.logical_name())
  if repository.is_main_repo():
    cloned_main_repo_name = env_var_repo.resource_name()
  clone_repositories.append((repository.resource_name(), env_var_repo.resource_name()))
else:
  env_var_repo = repository
%>
% for (env_var_name, env_var_value) in env_var_repo.env_var_value_dict().items():
      ${env_var_name}: ${env_var_value}
% endfor
% endfor
% for variable_name, value in job_step.inputs().items():
      ${variable_name.upper().replace('-','_')}: ${value}
% endfor
% for variable_name, value in job_step.outputs().items():
      ${variable_name.upper().replace('-','_')}: ${value}
% endfor
      BUILD_JOB_NAME: ${job_variant.job_name()}
      CONCOURSE_CURRENT_CFG: ${config_set.name()}
      CONCOURSE_CURRENT_TEAM: ${target_team}
      PIPELINE_NAME: ${pipeline_name}
  % if secret_cfg:
      SECRET_CIPHER_ALGORITHM: ${secret_cfg.cipher_algorithm().value}
      % if secret_cfg.generation():
      SECRET_KEY: ${secret_cfg.key_from_gen().decode('utf-8')}
      % else:
      SECRET_KEY: ${secret_cfg.key().decode('utf-8')}
      % endif
  % endif
      SECRETS_SERVER_CONCOURSE_CFG_NAME: ${secrets_server_cfg_url_path}
      SECRETS_SERVER_ENDPOINT: ${secrets_server_cfg.endpoint_url()}
% if has_component_descriptor_trait(job_variant):
      COMPONENT_NAME: ${job_variant.trait('component_descriptor').component_name()}

% endif
% for name, expression in job_step.variables().items():
      ${name}: '${eval(expression, {
        'pipeline': pipeline_definition,
        'pipeline_descriptor': pipeline,
        })}'
% endfor
% if job_step.script_type() == ScriptType.BOURNE_SHELL:
    run:
      path: /bin/sh
      args:
      - -exc
  % if job_step.name != 'publish':
      - |
  % else:
      - "echo this is a dummy step"
  % endif
% elif job_step.script_type() == ScriptType.PYTHON3:
    run:
      path: /usr/bin/env
      args:
      - python3
      - -c
      - |
        os = __import__('os')
        CC_ROOT_DIR = os.path.abspath('.')
        os.environ['CC_ROOT_DIR'] = CC_ROOT_DIR
        del os
        import ci.log
        import logging
        ci.log.configure_default_logging(stdout_level=logging.INFO)
        del ci
        del logging
% else:
  <% raise ValueError('unsupported script type') %>
% endif
% if not job_step.is_synthetic:
  % if has_pr_trait(job_variant):
        export PULLREQUEST_ID=$(git config -f "${job_variant.main_repository().resource_name()}/.git/config" pullrequest.id)
        export PULLREQUEST_URL=$(git config -f "${job_variant.main_repository().resource_name()}/.git/config" pullrequest.url)
  % endif
  % if has_version_trait(job_variant):
        export EFFECTIVE_VERSION=$(cat ${job_step.input('version_path')}/version)
    % if job_variant.trait('version').inject_effective_version():
        # copy processed version information to VERSION file
        <%
        version_file_path = os.path.join(
          source_repo.resource_name(),
          job_variant.trait('version').versionfile_relpath()
        )
        %>
        cp "${job_step.input('version_path')}/version" "${version_file_path}"
    % endif
  % endif
  % for from_path, to_path in clone_repositories:
        # clone repositories for outputting
        # cp directory recursively (resorting to least common deniminator defined by POSIX)
        tar c -C ${from_path} . | tar x -C ${to_path}
  % endfor
  % if clone_repositories:
        # init git config
        git config --global user.name "${github.credentials().username()}"
        git config --global user.email "${github.credentials().email_address()}"
  % endif
<%
  if cloned_main_repo_name:
    prefix = (cloned_main_repo_name, '.ci')
  else:
    prefix = (source_repo.resource_name(), '.ci')
  executable_file = job_step.executable(prefix=prefix).rstrip()
  executable_cmd = job_step.execute(prefix=prefix).rstrip()
%>
        if readlink -f .>/dev/null 2>&1; then
          CC_ROOT_DIR="$(readlink -f .)"
          export CC_ROOT_DIR
        else
          echo "WARNING: no readlink available - CC_ROOT_DIR not set"
        fi
        if [ -x "${executable_file}" ]; then
          ${executable_cmd}
        elif [ -f "${executable_file}" ]; then
          echo "ERROR: file ${executable_file} is not executable."
          exit 1
        else
          echo "ERROR: no executable found at ${executable_file}"
          exit 1
        fi
% elif job_step.name == 'prepare':
        ${prepare_step(job_step=job_step, job_variant=job_variant, indent=8)}
% elif job_step.name == 'publish':
        ${publish_step(job_step=job_step, job_variant=job_variant, indent=8)}
% elif job_step.name == 'version':
        ${version_step(job_step=job_step, job_variant=job_variant, indent=8)}
% elif job_step.name == 'release':
        ${release_step(job_step=job_step, job_variant=job_variant, github_cfg=github, indent=8)}
% elif job_step.name == 'meta':
        ${meta_step(job_step=job_step, job_variant=job_variant, indent=8, additional_meta_data=meta_data_dict)}
% elif job_step.name == 'rm_pr_label':
        ${rm_pr_label_step(job_step=job_step, job_variant=job_variant, github_cfg=github, indent=8)}
% elif job_step.name == DEFAULT_COMPONENT_DESCRIPTOR_STEP_NAME:
<%
  if has_publish_trait(job_variant):
    image_descriptors_for_variant = {
      descriptor.name(): descriptor
      for descriptor in job_variant.trait('publish').dockerimages()
    }
  else:
    image_descriptors_for_variant = {}
%>
        ${component_descriptor_step(job_step=job_step, job_variant=job_variant, output_image_descriptors=image_descriptors_for_variant, indent=8)}
% elif job_step.name == 'update_component_dependencies':
        ${update_component_deps_step(job_step=job_step, job_variant=job_variant, github_cfg_name=source_repo_github_cfg_name, indent=8)}
% elif job_step.name.startswith('build_oci_image'):
        ${build_oci_image_step(job_step=job_step, job_variant=job_variant, cfg_set=config_set, indent=8)}
% elif job_step.name == 'create_draft_release_notes':
        ${draft_release_step(job_step=job_step, job_variant=job_variant, github_cfg=github, indent=8)}
% elif job_step.name == 'scan_container_images':
        ${scan_container_images_step(job_step=job_step, job_variant=job_variant, cfg_set=config_set, indent=8)}
% elif job_step.name == 'malware-scan':
        ${malware_scan_step(job_step=job_step, job_variant=job_variant, cfg_set=config_set, indent=8)}
% elif job_step.name == 'os-id-scan':
        ${os_id_step(job_step=job_step, job_variant=job_variant, cfg_set=config_set, indent=8)}
% elif job_step.name == 'scan_sources':
        ${scan_sources_step(job_step=job_step, job_variant=job_variant, cfg_set=config_set, indent=8)}
% elif job_step.name == 'replicate_pipelines':
        ${replicate_pipelines_step(step=job_step, job=job_variant, job_mapping=job_mapping, indent=8)}
% elif job_step.name == 'replicate_secrets':
        ${replicate_secrets_step(step=job_step, job=job_variant, job_mapping=job_mapping, indent=8)}
% endif
% endif
% if job_step.publish_repository_names() and not job_variant.has_trait('pull-request'):
<%
publish_to_repo_dict = job_step.publish_repository_dict()
%>
  ensure:
    in_parallel:
% for publish_to_repo_name, publish_options in publish_to_repo_dict.items():
<%
if not publish_options:
  publish_options = {}
%>
      - put: ${job_variant.publish_repository(publish_to_repo_name).resource_name()}
        params:
          repository: ${job_variant.publish_repository(publish_to_repo_name).resource_name()}
          rebase: ${not (publish_options.get('force_push', False))}
          force: ${publish_options.get('force_push', False)}
% endfor
% endif
</%def>

<%def name="step(indent, job_variant, job_step)" filter="indent_func(indent),trim">
<%
notification_policy = job_step.notification_policy()
if notification_policy is StepNotificationPolicy.NO_NOTIFICATION:
  render_notification_step = False
elif notification_policy is StepNotificationPolicy.NOTIFY_PULL_REQUESTS:
  render_notification_step = True
else:
  raise NotImplementedError(notification_policy)

notify_pull_requests = (
  has_pr_trait(job_variant)
  and job_step.notification_policy() is StepNotificationPolicy.NOTIFY_PULL_REQUESTS
)
%>
- do:
% if notify_pull_requests:
  ${update_pr_status(2, job_variant, job_step, 'pending')}
% endif
  ${execute(2, job_step, job_variant)}
% if notify_pull_requests:
  ${update_pr_status(2, job_variant, job_step, 'success')}
% endif
% if render_notification_step:
  ${notification(2, job_variant, job_step, 'error')}
% endif
</%def>

<%def name="job(job_variant)">
- name: ${job_variant.job_name()}
  serial: ${'true' if suppress_parallel_execution(job_variant) else 'false'}
% if has_options_trait(job_variant):
  build_logs_to_retain: ${job_variant.trait('options').build_logs_to_retain()}
  public: ${'true' if job_variant.trait('options').public_build_logs() else 'false'}
% endif
  plan:
% for repository in job_variant.repositories():
  - get: ${repository.resource_name()}
  % if repository.should_trigger():
    trigger: true
  % endif
% endfor
% if has_cron_trait(job_variant):
  - get: "${job_variant.trait('cronjob').resource_name()}"
    trigger: true
% endif
% if job_variant.publish_repositories() and not has_pr_trait(job_variant):
  % for publish_repo in job_variant.publish_repositories():
  # force concourse to rebase the source repositories we are going to write to later.
  # otherwise, we may try to create a new commit onto an outdated branch head
  <%
  # determine the corresponding source (input) repository
  source_repo = job_variant.repository(publish_repo.logical_name())
  %>
  - put: ${source_repo.resource_name()}
    params:
      repository: ${source_repo.resource_name()}
      rebase: true
  % endfor
% endif
% for parallel_steps in job_variant.ordered_steps():
  - in_parallel:
% for step_name in parallel_steps:
    ${step(4, job_variant, job_variant.step(step_name))}
% endfor
% endfor
</%def>

% if background_image is not none:
display:
  background_image: "${background_image}"
% endif

jobs:
% for variant in pipeline_definition.variants():
${job(variant)}
% endfor
...
