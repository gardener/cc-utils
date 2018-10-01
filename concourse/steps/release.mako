<%def
  name="release_step(job_step, job_variant, github_cfg, indent)",
  filter="indent_func(indent),trim"
>
<%
from makoutil import indent_func
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
/cc/utils/cli.py githubutil release_and_prepare_next_dev_cycle \
  % if has_component_descriptor_trait:
  --component-descriptor-file-path ${component_descriptor_file_path} \
  % endif
  % if has_slack_trait:
  --slack-cfg-name "${slack_cfg_name}" \
  --slack-channel "${slack_channel}" \
  % endif
  % if release_callback_path:
  --release-commit-callback "${release_callback_path}" \
  % endif
   --github-cfg-name ${github_cfg.name()} \
   --github-repository-name ${repo.repo_name()} \
   --github-repository-owner ${repo.repo_owner()} \
   --repository-version-file-path ${version_trait.versionfile_relpath()} \
   --repository-branch ${repo.branch()} \
   --release-version $(cat "${version_file}") \
   --should-generate-release-notes \
   --repo-dir ${repo.resource_name()} \
   --version-operation "${version_op}"
</%def>
