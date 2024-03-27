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
ReleaseNotesPolicy = concourse.model.traits.release.ReleaseNotesPolicy
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

release_notes_policy = release_trait.release_notes_policy()
if release_notes_policy is ReleaseNotesPolicy.DEFAULT:
  process_release_notes = True
elif release_notes_policy is ReleaseNotesPolicy.DISABLED:
  process_release_notes = False
else:
  raise ValueError(release_notes_policy)

release_commit_publishing_policy = release_trait.release_commit_publishing_policy()
if release_commit_publishing_policy is ReleaseCommitPublishingPolicy.TAG_ONLY:
  merge_back = False
  push_release_commit = False
elif release_commit_publishing_policy is ReleaseCommitPublishingPolicy.TAG_AND_PUSH_TO_BRANCH:
  merge_back = False
  push_release_commit = True
elif release_commit_publishing_policy is ReleaseCommitPublishingPolicy.TAG_AND_MERGE_BACK:
  push_release_commit = False
  merge_back = True
else:
  raise ValueError(release_commit_publishing_policy)

mergeback_commit_msg_prefix = release_trait.merge_release_to_default_branch_commit_message_prefix()

assets = release_trait.assets
%>
import hashlib
import tempfile
import zlib

import ccc.concourse
import ccc.github
import ccc.oci
import ci.util
import cnudie.iter
import cnudie.retrieve
import cnudie.upload
import cnudie.util
import cnudie.validate
import concourse.steps.component_descriptor_util as cdu
import concourse.steps.release
import concourse.model.traits.version
import concourse.model.traits.release
import concourse.util
import gci.componentmodel as cm
import github.util
import gitutil

import git

import traceback

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

<%
import concourse.steps
template = concourse.steps.step_template('component_descriptor')
ocm_repository_lookup = template.get_def('ocm_repository_lookup').render
%>
${ocm_repository_lookup(ocm_repository_mappings)}

component_descriptor_lookup = cnudie.retrieve.create_default_component_descriptor_lookup(
  ocm_repository_lookup=ocm_repository_lookup,
)
version_lookup = cnudie.retrieve.version_lookup(
  ocm_repository_lookup=ocm_repository_lookup,
)

component_descriptor = cdu.component_descriptor_from_dir(
  '${job_step.input('component_descriptor_dir')}'
)
component = component_descriptor.component

% if assets:
oci_client = ccc.oci.oci_client()
component_descriptor_target_ref = cnudie.util.target_oci_ref(component=component)
concourse_client = ccc.concourse.client_from_env()
current_build = concourse.util.find_own_running_build()
build_plan = current_build.plan()

main_source = cnudie.util.main_source(component)
main_source_ref = {
  'name': main_source.name,
  'version': main_source.version,
}
% for asset in assets:
task_id = build_plan.task_id(task_name='${asset.step_name}')
build_events = concourse_client.build_events(build_id=current_build.id())
leng = 0
hash = hashlib.sha256()
compressor = zlib.compressobj(wbits=31) # 31: be compatible to gzip
with tempfile.TemporaryFile() as f:
  for line in build_events.iter_buildlog(task_id=task_id):
    line = line.encode('utf-8')
    buf = compressor.compress(line)
    leng += len(buf)
    hash.update(buf)
    f.write(buf)
  buf = compressor.flush()
  leng += len(buf)
  hash.update(buf)
  f.write(buf)

  f.seek(0)
  logger.info(f'pushing blob for asset ${asset.name} to {component_descriptor_target_ref}')
  digest = f'sha256:{hash.hexdigest()}'
  oci_client.put_blob(
    image_reference=component_descriptor_target_ref,
    digest=digest,
    octets_count=leng,
    data=f,
  )
component.resources.append(
  cm.Resource(
    name='${asset.name}',
    version=version_str,
    type='text/plain',
    access=cm.LocalBlobAccess(
      mediaType='application/gzip',
      localReference=digest,
      size=leng,
    ),
    labels=${asset.ocm_labels},
    srcRefs=[{
      'identitySelector': main_source_ref,
    },]
  ),
)
% endfor
% endif

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
logger.info(f'will fetch and rebase refs/heads/{branch}')
upstream_commit_sha = git_helper.fetch_head(
    f'refs/heads/{branch}'
).hexsha
git_helper.rebase(commit_ish=upstream_commit_sha)
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

% if push_release_commit:
git_helper.push(
  from_ref=release_commit.hexsha,
  to_ref=branch,
)
% endif

tags = _calculate_tags(
  version=version_str,
  github_release_tag=${github_release_tag},
  git_tags=${git_tags},
)

if have_tag_conflicts(
  github_helper=github_helper,
  tags=tags,
):
  exit(1)


create_and_push_tags(
  git_helper=git_helper,
  tags=tags,
  release_commit=release_commit,
)

% if release_trait.release_on_github():
try:
  clean_draft_releases(
    github_helper=github_helper,
  )
except:
  logger.warning('An Error occurred whilst trying to remove draft-releases')
  traceback.print_exc()
  # keep going

github_release(
  github_helper=github_helper,
  release_tag=tags[0],
  release_version=version_str,
  component_name=component.name,
)
% endif

logger.info('validating component-descriptor')
nodes = cnudie.iter.iter(
  component=component,
  lookup=component_descriptor_lookup,
)
for validation_error in cnudie.validate.iter_violations(nodes=nodes):
  logger.warning(f'{validation_error=}')

tgt_ref = cnudie.util.target_oci_ref(component=component)
logger.info(f'publishing OCM-Component-Descriptor to {tgt_ref=}')
uploaded_oci_manifest_bytes = cnudie.upload.upload_component_descriptor(
  component_descriptor=component,
)
try:
  print(f'{uploaded_oci_manifest_bytes=}')
except:
  pass

% if release_trait.release_on_github():
upload_github_release_asset(
  github_helper=github_helper,
  github_release_tag=tags[0],
  component=component,
)
% endif

% if merge_back:
try:
  old_head = git_helper.repo.head

  create_and_push_mergeback_commit(
    git_helper=git_helper,
    github_helper=github_helper,
    tags=tags,
    branch=branch,
    merge_commit_message_prefix='${mergeback_commit_msg_prefix or ''}',
    release_commit=release_commit,
  )
except git.GitCommandError:
  # do not fail release upon mergeback-errors; tags have already been pushed. Missing merge-back
  # will only cause bump-commit to not be merged back to default branch (which is okay-ish)
  logger.warning(f'pushing of mergeback-commit failed; release continues, you need to bump manually')
  traceback.print_exc()
  git_helper.repo.head.reset(
    commit=old_head.commit,
    index=True,
    working_tree=True,
  )
% endif

% if process_release_notes:
try:
  release_notes_md = collect_release_notes(
    repo_dir=repo_dir,
    release_version=version_str,
    component=component,
    component_descriptor_lookup=component_descriptor_lookup,
    version_lookup=version_lookup,
  )
except:
  logger.warning('an error occurred whilst trying to collect release-notes')
  logger.warning('release will continue')
  traceback.print_exc()
  release_notes_md = None

try:
  github_helper.update_release_notes(
    tag_name=version_str,
    body=release_notes_md,
    component_name=component.name,
  )
  git_helper.push('refs/notes/commits', 'refs/notes/commits')
except:
  logger.warning('an error occurred whilst trying to update release-notes')
  logger.warning('release will continue')
  traceback.print_exc()
% else:
release_notes_md = None
% endif

% if version_operation != version.NOOP:
create_and_push_bump_commit(
  git_helper=git_helper,
  repo_dir=repo_dir,
  release_version=version_str,
  release_commit=release_commit,
  merge_release_back_to_default_branch_commit='HEAD',
  version_interface=version_interface,
  version_path=version_path,
  repository_branch=branch,
  version_operation='${version_operation}',
  prerelease_suffix='dev',
  publishing_policy=concourse.model.traits.release.ReleaseCommitPublishingPolicy(
    '${release_trait.release_commit_publishing_policy().value}'
  ),
  commit_message_prefix='${next_cycle_commit_message_prefix or ''}',
  % if next_version_callback_path:
  next_version_callback='${next_version_callback_path}',
  % endif
)
% endif

% if has_slack_trait:
  % for slack_channel_cfg in slack_channel_cfgs:
try:
  if release_notes_md:
    post_to_slack(
      release_notes_markdown=release_notes_md,
      component=component,
      slack_cfg_name='${slack_channel_cfg["slack_cfg_name"]}',
      slack_channel='${slack_channel_cfg["channel_name"]}',
    )
except:
  logger.warning('An error occurred whilst trying to post release-notes to slack')
  traceback.print_exc()
  % endfor
% endif
</%def>
