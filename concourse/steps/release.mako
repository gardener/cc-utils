<%def
  name="release_step(job_step, job_variant, github_cfg, indent)",
  filter="indent_func(indent),trim"
>
<%
from makoutil import indent_func
from concourse.steps import step_lib
import os
version_file = job_step.input('version_path') + '/version'
release_trait = job_variant.trait('release')
version_trait = job_variant.trait('version')
version_op = release_trait.nextversion()

has_slack_trait = job_variant.has_trait('slack')
if has_slack_trait:
  slack_trait = job_variant.trait('slack')

  slack_channel_cfgs = slack_trait.channel_cfgs()

  slack_channel_cfg = slack_channel_cfgs[slack_trait.default_channel()]
  slack_channel = slack_channel_cfg.channel_name()
  slack_cfg_name = slack_channel_cfg.slack_cfg_name()

repo = job_variant.main_repository()
has_component_descriptor_trait = job_variant.has_trait('component_descriptor')
if has_component_descriptor_trait:
  component_descriptor_file_path = os.path.join(
    job_step.input('component_descriptor_dir'),
    'component_descriptor'
  )

release_callback_path = release_trait.release_callback_path()
%>

${step_lib('release')}

with open('${version_file}') as f:
  version_str = f.read()

release_and_prepare_next_dev_cycle(
  % if has_component_descriptor_trait:
  component_descriptor_file_path='${component_descriptor_file_path}',
  % endif
  % if has_slack_trait:
  slack_cfg_name='${slack_cfg_name}',
  slack_channel='${slack_channel}',
  % endif
  % if release_callback_path:
  release_commit_callback='${release_callback_path}',
  % endif
  rebase_before_release=${release_trait.rebase_before_release()},
  github_cfg_name='${github_cfg.name()}',
  github_repository_name='${repo.repo_name()}',
  github_repository_owner='${repo.repo_owner()}',
  repository_version_file_path='${version_trait.versionfile_relpath()}',
  repository_branch='${repo.branch()}',
  release_version=version_str,
  repo_dir='${repo.resource_name()}',
  version_operation='${version_op}',
)
</%def>
