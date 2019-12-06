<%def
  name="update_component_deps_step(job_step, job_variant, github_cfg_name, indent)",
  filter="indent_func(indent),trim"
>
<%
from concourse.steps import step_lib
from makoutil import indent_func
main_repo = job_variant.main_repository()
repo_name = main_repo.logical_name().upper()
update_component_deps_trait = job_variant.trait('update_component_deps')
set_dependency_version_script_path = update_component_deps_trait.set_dependency_version_script_path()
after_merge_callback = update_component_deps_trait.after_merge_callback()
%>

import os
import subprocess
import sys
from tempfile import TemporaryDirectory

import ci.util
import ctx
import github.util
import gitutil
import product.model
import product.util
import version

from concourse.model.traits.update_component_deps import (
    MergePolicy,
)
from github.release_notes.util import ReleaseNotes
from github.util import (
    GitHubRepositoryHelper,
    GitHubRepoBranch,
)
from ci.util import check_env

${step_lib('update_component_deps')}

# must point to this repository's root directory
REPO_ROOT = os.path.abspath(check_env('${repo_name}_PATH'))
REPO_BRANCH = check_env('${repo_name}_BRANCH')
REPO_OWNER, REPO_NAME = check_env('${repo_name}_GITHUB_REPO_OWNER_AND_NAME').split('/')


cfg_factory = ci.util.ctx().cfg_factory()
github_cfg_name = '${github_cfg_name}'
github_cfg=cfg_factory.github(github_cfg_name)


component_resolver = product.util.ComponentResolver(cfg_factory=cfg_factory)
component_descriptor_resolver = product.util.ComponentDescriptorResolver(cfg_factory=cfg_factory)

# indicates whether or not an upstream component was defined as a reference
UPGRADE_TO_UPSTREAM = 'UPSTREAM_COMPONENT_NAME' in os.environ

ci.util.info(f'Upgrade to upstream: {UPGRADE_TO_UPSTREAM}')


def _component(product_descriptor, component_name):
    component = [c for c in product_descriptor.components() if c.name() == component_name]
    component_count = len(component)
    try:
      print('component names:', [c.name() for c in product_descriptor.components()])
    except:
      pass
    if component_count == 1:
        return component[0]
    elif component_count < 1:
        ci.util.fail('Did not find component {cn}'.format(cn=component_name))
    elif component_count > 1:
        ci.util.fail('Found more than one component with name ' + component_name)
    else:
        raise NotImplementedError # this line should never be reached


def current_component():
    product = current_product_descriptor()
    component_name = check_env('COMPONENT_NAME')
    return _component(product, component_name=component_name)


def upstream_reference_component():
    component_name = check_env('UPSTREAM_COMPONENT_NAME')
    latest_version = component_resolver.latest_component_version(component_name)

    component_reference = product.model.ComponentReference.create(
        name=component_name,
        version=latest_version,
    )

    reference_product = component_descriptor_resolver.retrieve_descriptor(
        component_reference=component_reference,
    )

    reference_component = _component(
        product_descriptor=reference_product,
        component_name=component_name,
    )

    return reference_component


def close_obsolete_pull_requests(upgrade_pull_requests, reference_component):
    open_pull_requests = [
        pr for pr in upgrade_pull_requests
        if pr.pull_request.state == 'open'
    ]
    obsolete_upgrade_requests = [
        pr for pr in open_pull_requests
        if pr.is_obsolete(reference_component=reference_component)
    ]

    for obsolete_request in obsolete_upgrade_requests:
        obsolete_request.purge()


def upgrade_pr_exists(reference, upgrade_requests):
    return any(
        [upgrade_rq.target_matches(reference=reference) for upgrade_rq in upgrade_requests]
    )


def create_upgrade_pr(from_ref, to_ref, pull_request_util):
    ls_repo = pull_request_util.repository
    repo_dir = str(REPO_ROOT)

    # have component create upgradation diff
    upgrade_script_path = os.path.join(REPO_ROOT, '${set_dependency_version_script_path}')
    cmd_env = os.environ.copy()
    cmd_env['DEPENDENCY_TYPE'] = to_ref.type_name()
    cmd_env['DEPENDENCY_NAME'] = to_ref.name()
    cmd_env['DEPENDENCY_VERSION'] = to_ref.version()
    cmd_env['REPO_DIR'] = repo_dir
    cmd_env['GITHUB_CFG_NAME'] = github_cfg_name

    # pass type-specific attributes
    if to_ref.type_name() == 'container_image':
      cmd_env['DEPENDENCY_IMAGE_REFERENCE'] = to_ref.image_reference()

    subprocess.run(
        [str(upgrade_script_path)],
        check=True,
        env=cmd_env
    )
    commit_msg = 'Upgrade {cn}\n\nfrom {ov} to {nv}'.format(
        cn=to_ref.name(),
        ov=from_ref.version(),
        nv=to_ref.version(),
    )

    githubrepobranch = GitHubRepoBranch(
        github_config=github_cfg,
        repo_owner=REPO_OWNER,
        repo_name=REPO_NAME,
        branch=REPO_BRANCH,
    )

    # mv diff into commit and push it
    helper = gitutil.GitHelper.from_githubrepobranch(
        githubrepobranch=githubrepobranch,
        repo_path=repo_dir,
    )
    commit = helper.index_to_commit(message=commit_msg)
    ci.util.info(f'commit for upgrade-PR: {commit.hexsha}')

    new_branch_name = ci.util.random_str(prefix='ci-', length=12)
    head_sha = ls_repo.ref('heads/' + REPO_BRANCH).object.sha
    ls_repo.create_ref('refs/heads/' + new_branch_name, head_sha)

    def rm_pr_branch():
      ls_repo.ref('heads/' + new_branch_name).delete()

    try:
      helper.push(from_ref=commit.hexsha, to_ref='refs/heads/' + new_branch_name)
    except:
      ci.util.warning('an error occurred - removing now useless pr-branch')
      rm_pr_branch()
      raise

    helper.repo.git.checkout('.')

    try:
      with TemporaryDirectory() as temp_dir:
          from_github_cfg = cfg_factory.github(from_ref.config_name())
          from_github_helper = GitHubRepositoryHelper(
              github_cfg=from_github_cfg,
              owner=from_ref.github_organisation(),
              name=from_ref.github_repo(),
          )
          from_git_helper = gitutil.GitHelper.clone_into(
              target_directory=temp_dir,
              github_cfg=from_github_cfg,
              github_repo_path=from_ref.github_repo_path()
          )
          commit_range = '{from_version}..{to_version}'.format(
              from_version=from_ref.version(),
              to_version=to_ref.version()
          )
          release_note_blocks = ReleaseNotes.create(
              github_helper=from_github_helper,
              git_helper=from_git_helper,
              commit_range=commit_range
          ).release_note_blocks()
          if release_note_blocks:
              text = '*Release Notes*:\n{blocks}'.format(
                  blocks=release_note_blocks
              )
          else:
              text = pull_request_util.retrieve_pr_template_text()
    except:
      ci.util.warning('an error occurred during release notes processing (ignoring)')
      text = None
      import traceback
      ci.util.warning(traceback.format_exc())

    pull_request = ls_repo.create_pull(
            title=github.util.PullRequestUtil.calculate_pr_title(
                reference=to_ref,
                from_version=from_ref.version(),
                to_version=to_ref.version()
            ),
            base=REPO_BRANCH,
            head=new_branch_name,
            body=text,
    )

    if MergePolicy('${update_component_deps_trait.merge_policy().value}') == MergePolicy.MANUAL:
        return

    # auto-merge - todo: make configurable (e.g. merge method)
    pull_request.merge()
    rm_pr_branch()

% if after_merge_callback:
    subprocess.run(
        [os.path.join(repo_dir, '${after_merge_callback}')],
        check=True,
        env=cmd_env
    )
% endif


reference_product = current_product_descriptor()

pull_request_util = github.util.PullRequestUtil(
    owner=REPO_OWNER,
    name=REPO_NAME,
    default_branch=REPO_BRANCH,
    github_cfg=github_cfg,
)

# hack / workaround: rebase to workaround concourse sometimes not refresing git-resource
git_helper = gitutil.GitHelper(
  repo=REPO_ROOT,
  github_cfg=github_cfg,
  github_repo_path=f'{REPO_OWNER}/{REPO_NAME}',
)
git_helper.rebase(
  commit_ish=REPO_BRANCH,
)

upgrade_pull_requests = pull_request_util.enumerate_upgrade_pull_requests(state_filter='all')

close_obsolete_pull_requests(
    upgrade_pull_requests=upgrade_pull_requests,
    reference_component=current_component(),
)


immediate_dependencies = current_component().dependencies()

if UPGRADE_TO_UPSTREAM:
  def determine_reference_version(component_name):
    return _component(upstream_reference_component().dependencies(), component_name).version()
else:
  def determine_reference_version(component_name):
    return component_resolver.latest_component_version(component_name)


# find components that need to be upgraded
for reference in product.util.greatest_references(immediate_dependencies.components()):
    latest_version = determine_reference_version(reference.name())
    latest_version_semver = version.parse_to_semver(latest_version)
    latest_cref = product.model.ComponentReference.create(
      name=reference.name(),
      version=latest_version,
    )
    print(f'latest_version: {latest_version}, ref: {reference}')
    if latest_version_semver <= version.parse_to_semver(reference.version()):
        ci.util.info('skipping outdated component upgrade: {n}; our version: {ov}, found: {fv}'.format(
          n=reference.name(),
          ov=str(reference.version()),
          fv=latest_version,
          )
        )
        continue
    elif upgrade_pr_exists(reference=latest_cref, upgrade_requests=upgrade_pull_requests):
        ci.util.info('skipping upgrade (PR already exists): ' + reference.name())
        continue
    else:
        ci.util.info('creating upgrade PR: {n}->{v}'.format(
          n=reference.name(),
          v=latest_version,
          )
        )
        to_ref = product.model.ComponentReference.create(
            name=reference.name(),
            version=latest_version,
        )
        create_upgrade_pr(from_ref=reference, to_ref=to_ref, pull_request_util=pull_request_util)
</%def>
