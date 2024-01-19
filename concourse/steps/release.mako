<%def
  name="release_step(job_step, job_variant, github_cfg, indent)",
  filter="indent_func(indent),trim"
>
<%
import os

from makoutil import indent_func
from concourse.steps import step_lib
import ci.util
import concourse.steps.component_descriptor_util as cdu
import concourse.model.traits.version
import concourse.model.traits.release
import gci.componentmodel
import version
ReleaseCommitPublishingPolicy = concourse.model.traits.release.ReleaseCommitPublishingPolicy
VersionInterface = concourse.model.traits.version.VersionInterface
version_file = job_step.input('version_path') + '/version'
release_trait = job_variant.trait('release')

if (release_commit_callback_image_reference := release_trait.release_callback_image_reference()):
  release_commit_callback_image_reference = release_commit_callback_image_reference.image_reference()

version_trait = job_variant.trait('version')
version_interface = version_trait.version_interface()
version_operation = release_trait.nextversion()
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
import concourse.model.traits.release
import github.util
import gitutil

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

component_descriptor = cdu.component_descriptor_from_dir(
  '${job_step.input('component_descriptor_dir')}'
)

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

git_helper = gitutil.GitHelper.from_githubrepobranch(
  githubrepobranch=githubrepobranch,
  repo_path=repo_dir,
)
github_helper = github.util.GitHubRepositoryHelper.from_githubrepobranch(githubrepobranch)
branch = githubrepobranch.branch()

% if release_trait.rebase_before_release():
rebase(
  git_helper=git_helper,
  branch=branch,
)
% endif

release_commit = create_release_commit(
  git_helper=git_helper,
  branch=branch,
  version=version_str,
  version_interface=version_interface,
  version_path=version_path,
% if release_commit_message_prefix:
  release_commit_message_prefix='${release_commit_message_prefix}',
% endif
% if release_callback_path:
  release_commit_callback='${release_callback_path}',
  release_commit_callback_image_reference=release_commit_callback_image_reference,
% endif
)

% if release_trait.release_commit_publishing_policy() is ReleaseCommitPublishingPolicy.TAG_AND_PUSH_TO_BRANCH:
git_helper.push(
  from_ref=release_commit.hexsha,
  to_ref=branch,
)
% endif

merge_release_back_to_default_branch_commit = release_and_prepare_next_dev_cycle(
  component_descriptor=component_descriptor,
  branch=branch,
  github_helper=github_helper,
  git_helper=git_helper,
  release_commit=release_commit,
  % if has_slack_trait:
  slack_channel_configs=${slack_channel_cfgs},
  % endif
  release_on_github=${release_trait.release_on_github()},
  githubrepobranch=githubrepobranch,
  repo_dir=repo_dir,
  release_version=version_str,
  release_notes_policy='${release_trait.release_notes_policy().value}',
  release_commit_publishing_policy='${release_trait.release_commit_publishing_policy().value}',
  % if merge_release_to_default_branch_commit_message_prefix:
  merge_release_to_default_branch_commit_message_prefix='${merge_release_to_default_branch_commit_message_prefix}',
  % endif
  github_release_tag=${github_release_tag},
  git_tags=${git_tags},
  mapping_config=mapping_config,
)

% if version_operation != version.NOOP:
create_and_push_bump_commit(
  git_helper=git_helper,
  repo_dir=repo_dir,
  release_version=version_str,
  release_commit=release_commit,
  merge_release_back_to_default_branch_commit=merge_release_back_to_default_branch_commit,
  version_interface=version_interface,
  version_path=version_path,
  repository_branch=branch,
  version_operation='${version_operation}',
  prerelease_suffix='dev',
  publishing_policy=concourse.model.traits.release.ReleaseCommitPublishingPolicy(
    '${release_trait.release_commit_publishing_policy().value}'
  ),
  next_cycle_commit_message_prefix='${next_cycle_commit_message_prefix}',
  % if next_version_callback_path:
  next_version_callback='${next_version_callback_path}',
  % endif
)
% endif
</%def>
