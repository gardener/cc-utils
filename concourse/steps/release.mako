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
release_commit_message_prefix = release_trait.release_commit_message_prefix()
next_cycle_commit_message_prefix = release_trait.next_cycle_commit_message_prefix()

has_slack_trait = job_variant.has_trait('slack')
if has_slack_trait:
  slack_trait = job_variant.trait('slack')

  slack_channel_cfgs = slack_trait.channel_cfgs()

  slack_channel_cfg = slack_channel_cfgs[slack_trait.default_channel()]
  slack_channel = slack_channel_cfg.channel_name()
  slack_cfg_name = slack_channel_cfg.slack_cfg_name()

repo = job_variant.main_repository()

component_descriptor_file_path = os.path.join(
  job_step.input('component_descriptor_dir'),
  'component_descriptor'
)
component_descriptor_v2_path = os.path.join(
  job_step.input('component_descriptor_dir'),
  'component_descriptor_v2', # XXX deduplicate -> component_descriptor_util.py
)

release_callback_path = release_trait.release_callback_path()
next_version_callback_path = release_trait.next_version_callback_path()
%>
import ccc.github
import ci.util

${step_lib('release')}

with open('${version_file}') as f:
  version_str = f.read()

repo_dir = existing_dir('${repo.resource_name()}')
repository_branch = '${repo.branch()}'

github_cfg = ccc.github.github_cfg_for_hostname('${repo.repo_hostname()}')
github_repo_path = '${repo.repo_owner()}/${repo.repo_name()}'

githubrepobranch = GitHubRepoBranch(
    github_config=github_cfg,
    repo_owner='${repo.repo_owner()}',
    repo_name='${repo.repo_name()}',
    branch=repository_branch,
)

release_and_prepare_next_dev_cycle(
  component_descriptor_file_path='${component_descriptor_file_path}',
  component_descriptor_v2_path='${component_descriptor_v2_path}',
  % if has_slack_trait:
  slack_cfg_name='${slack_cfg_name}',
  slack_channel='${slack_channel}',
  % endif
  % if release_callback_path:
  release_commit_callback='${release_callback_path}',
  % endif
  % if next_version_callback_path:
  next_version_callback='${next_version_callback_path}',
  % endif
  rebase_before_release=${release_trait.rebase_before_release()},
  githubrepobranch=githubrepobranch,
  repo_dir=repo_dir,
  repository_version_file_path='${version_trait.versionfile_relpath()}',
  release_version=version_str,
  version_operation='${version_op}',
  release_notes_policy='${release_trait.release_notes_policy().value}',
  release_commit_publishing_policy='${release_trait.release_commit_publishing_policy().value}',
  % if release_commit_message_prefix:
  release_commit_message_prefix='${release_commit_message_prefix}',
  % endif
  % if next_cycle_commit_message_prefix:
  next_cycle_commit_message_prefix='${next_cycle_commit_message_prefix}',
  % endif
)
</%def>
