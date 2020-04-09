<%def
  name="update_component_deps_step(job_step, job_variant, github_cfg_name, indent)",
  filter="indent_func(indent),trim"
>
<%
from concourse.steps import step_lib
from makoutil import indent_func

main_repo = job_variant.main_repository()
repo_name = main_repo.repo_name()
repo_relpath = main_repo.resource_name()
repo_owner = main_repo.repo_owner()
repo_branch = main_repo.branch()

update_component_deps_trait = job_variant.trait('update_component_deps')
set_dependency_version_script_path = update_component_deps_trait.set_dependency_version_script_path()
after_merge_callback = update_component_deps_trait.after_merge_callback()
upstream_update_policy = update_component_deps_trait.upstream_update_policy()
%>

import os
import subprocess
import sys

import ci.util
import concourse.model.traits.update_component_deps
import ctx
import github.util
import gitutil
import product.model
import product.util
import version

from github.util import (
    GitHubRepoBranch,
)
from ci.util import check_env

${step_lib('update_component_deps')}

# must point to this repository's root directory
REPO_ROOT = os.path.abspath('${repo_relpath}')
REPO_BRANCH = '${repo_branch}'
REPO_OWNER = '${repo_owner}'
REPO_NAME = '${repo_name}'

cfg_factory = ci.util.ctx().cfg_factory()
github_cfg_name = '${github_cfg_name}'
github_cfg=cfg_factory.github(github_cfg_name)

githubrepobranch = GitHubRepoBranch(
    github_config=github_cfg,
    repo_owner=REPO_OWNER,
    repo_name=REPO_NAME,
    branch=REPO_BRANCH,
)

component_resolver = product.util.ComponentResolver(cfg_factory=cfg_factory)
component_descriptor_resolver = product.util.ComponentDescriptorResolver(cfg_factory=cfg_factory)

# indicates whether or not an upstream component was defined as a reference
upstream_component_name = os.environ.get('UPSTREAM_COMPONENT_NAME', None)
UPGRADE_TO_UPSTREAM = bool(upstream_component_name)

ci.util.info(f'Upgrade to upstream: {UPGRADE_TO_UPSTREAM}')

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
upstream_update_policy = concourse.model.traits.update_component_deps.UpstreamUpdatePolicy(
  '${upstream_update_policy.value}'
)


# find components that need to be upgraded
for reference in product.util.greatest_references(immediate_dependencies.components()):
    for latest_version in determine_reference_versions(
      component_name=reference.name(),
      reference_version=reference.version(),
      component_resolver=component_resolver,
      component_descriptor_resolver=component_descriptor_resolver,
      upstream_component_name=upstream_component_name,
      upstream_update_policy=upstream_update_policy,
    ):
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
            create_upgrade_pr(
              from_ref=reference,
              to_ref=to_ref,
              pull_request_util=pull_request_util,
              upgrade_script_path=os.path.join(REPO_ROOT, '${set_dependency_version_script_path}'),
              githubrepobranch=githubrepobranch,
              repo_dir=REPO_ROOT,
              github_cfg_name=github_cfg_name,
              cfg_factory=cfg_factory,
              merge_policy=MergePolicy('${update_component_deps_trait.merge_policy().value}'),
% if after_merge_callback:
              after_merge_callback='${after_merge_callback}',
% endif
            )
</%def>
