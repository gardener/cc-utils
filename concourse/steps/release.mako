<%def
  name="release_step(job_step, job_variant, github_cfg, indent)",
  filter="indent_func(indent),trim"
>
<%
from makoutil import indent_func
from concourse.steps import step_lib
import ci.util
import concourse.steps.component_descriptor_util as cdu
import concourse.model.traits.version
import gci.componentmodel
import os
VersionInterface = concourse.model.traits.version.VersionInterface
version_file = job_step.input('version_path') + '/version'
release_trait = job_variant.trait('release')

if (release_commit_callback_image_reference := release_trait.release_callback_image_reference()):
  release_commit_callback_image_reference = release_commit_callback_image_reference.image_reference()

version_trait = job_variant.trait('version')
version_interface = version_trait.version_interface()
version_op = release_trait.nextversion()
release_commit_message_prefix = release_trait.release_commit_message_prefix()
next_cycle_commit_message_prefix = release_trait.next_cycle_commit_message_prefix()
merge_release_to_default_branch_commit_message_prefix = release_trait.merge_release_to_default_branch_commit_message_prefix()

has_slack_trait = job_variant.has_trait('slack')
if has_slack_trait:
  slack_trait = job_variant.trait('slack')
  slack_channel_cfgs = [cfg.raw for cfg in slack_trait.channel_cfgs()]

github_release_tag = release_trait.github_release_tag()
git_tags = release_trait.git_tags()

repo = job_variant.main_repository()

component_descriptor_path = os.path.join(
  job_step.input('component_descriptor_dir'),
  cdu.component_descriptor_fname(gci.componentmodel.SchemaVersion.V2),
)

component_descriptor_trait = job_variant.trait('component_descriptor')
component_name = component_descriptor_trait.component_name()
ocm_repository_mappings = component_descriptor_trait.ocm_repository_mappings()

release_callback_path = release_trait.release_callback_path()
next_version_callback_path = release_trait.next_version_callback_path()
%>
import ccc.github
import ci.util
import cnudie.util
import concourse.steps.component_descriptor_util as cdu
import concourse.steps.release
import concourse.model.traits.version
import github.util

${step_lib('release')}

VersionInterface = concourse.model.traits.version.VersionInterface

with open('${version_file}') as f:
  version_str = f.read()

repo_dir = ci.util.existing_dir('${repo.resource_name()}')
repository_branch = '${repo.branch()}'

github_cfg = ccc.github.github_cfg_for_repo_url(
  ci.util.urljoin(
    '${repo.repo_hostname()}',
    '${repo.repo_path()}',
  )
)

githubrepobranch = github.util.GitHubRepoBranch(
    github_config=github_cfg,
    repo_owner='${repo.repo_owner()}',
    repo_name='${repo.repo_name()}',
    branch=repository_branch,
)

mapping_config = cnudie.util.OcmLookupMappingConfig.from_dict(
    raw_mappings = ${ocm_repository_mappings},
)

try:
  component_descriptor = cdu.component_descriptor_from_dir(
    '${job_step.input('component_descriptor_dir')}'
  )
  component_name = component_descriptor.component.name
except NotImplementedError:
  component_name = '${component_name}'

% if release_commit_callback_image_reference:
release_commit_callback_image_reference = '${release_commit_callback_image_reference}'
% else:
release_commit_callback_image_reference = None
% endif

version_interface = VersionInterface('${version_trait.version_interface().value}')
% if version_interface is VersionInterface.FILE:
version_path = '${os.path.join(repo.resource_name(), version_trait.versionfile_relpath())}'
% elif version_interface is VersionInterface.CALLBACK:
version_path = '${os.path.join(repo.resource_name(), version_trait.write_callback())}'
% else:
  <% raise ValueError('not implemented', version_interface) %>
% endif

print(f'{version_path=}')
print(f'{version_interface=}')

release_and_prepare_next_dev_cycle(
  component_name=component_name,
  component_descriptor_path='${component_descriptor_path}',
  % if has_slack_trait:
  slack_channel_configs=${slack_channel_cfgs},
  % endif
  % if release_callback_path:
  release_commit_callback='${release_callback_path}',
  % endif
  % if next_version_callback_path:
  next_version_callback='${next_version_callback_path}',
  % endif
  rebase_before_release=${release_trait.rebase_before_release()},
  release_on_github=${release_trait.release_on_github()},
  githubrepobranch=githubrepobranch,
  repo_dir=repo_dir,
  release_version=version_str,
  version_path=version_path,
  version_interface=version_interface,
  version_operation='${version_op}',
  release_notes_policy='${release_trait.release_notes_policy().value}',
  release_commit_publishing_policy='${release_trait.release_commit_publishing_policy().value}',
  release_commit_callback_image_reference=release_commit_callback_image_reference,
  % if release_commit_message_prefix:
  release_commit_message_prefix='${release_commit_message_prefix}',
  % endif
  % if merge_release_to_default_branch_commit_message_prefix:
  merge_release_to_default_branch_commit_message_prefix='${merge_release_to_default_branch_commit_message_prefix}',
  % endif
  % if next_cycle_commit_message_prefix:
  next_cycle_commit_message_prefix='${next_cycle_commit_message_prefix}',
  % endif
  github_release_tag=${github_release_tag},
  git_tags=${git_tags},
  mapping_config=mapping_config,
)
</%def>
