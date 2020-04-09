# Copyright (c) 2019-2020 SAP SE or an SAP affiliate company. All rights reserved. This file is
# licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import subprocess
import tempfile
import typing

import ci.util
import concourse.model.traits.update_component_deps
import gitutil
import github.util
import product.model
import product.util
import version
from concourse.model.traits.update_component_deps import (
    MergePolicy,
)
from github.util import (
    GitHubRepositoryHelper,
    GitHubRepoBranch,
)
from github.release_notes.util import ReleaseNotes


def current_product_descriptor():
    component_descriptor = os.path.join(
        ci.util.check_env('COMPONENT_DESCRIPTOR_DIR'),
        'component_descriptor',
    )
    return product.model.ComponentDescriptor.from_dict(
        ci.util.parse_yaml_file(component_descriptor),
    )


def current_component():
    product = current_product_descriptor()
    component_name = ci.util.check_env('COMPONENT_NAME')
    return _component(product, component_name=component_name)


def _component(
        product_descriptor: product.model.ComponentDescriptor,
        component_name: str,
    ):
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


def upstream_reference_component(
        component_resolver,
        component_descriptor_resolver,
        component_name: str,
    ):
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


def create_upgrade_pr(
        from_ref,
        to_ref,
        pull_request_util,
        upgrade_script_path,
        githubrepobranch: GitHubRepoBranch,
        repo_dir,
        github_cfg_name,
        cfg_factory,
        merge_policy,
        after_merge_callback=None,
    ):
    ls_repo = pull_request_util.repository

    # have component create upgradation diff
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

    # mv diff into commit and push it
    helper = gitutil.GitHelper.from_githubrepobranch(
        githubrepobranch=githubrepobranch,
        repo_path=repo_dir,
    )
    commit = helper.index_to_commit(message=commit_msg)
    ci.util.info(f'commit for upgrade-PR: {commit.hexsha}')

    new_branch_name = ci.util.random_str(prefix='ci-', length=12)
    repo_branch = githubrepobranch.branch()
    head_sha = ls_repo.ref(f'heads/{repo_branch}').object.sha
    ls_repo.create_ref(f'refs/heads/{new_branch_name}', head_sha)

    def rm_pr_branch():
      ls_repo.ref(f'heads/{new_branch_name}').delete()

    try:
      helper.push(from_ref=commit.hexsha, to_ref=f'refs/heads/{new_branch_name}')
    except:
      ci.util.warning('an error occurred - removing now useless pr-branch')
      rm_pr_branch()
      raise

    helper.repo.git.checkout('.')

    try:
      with tempfile.TemporaryDirectory() as temp_dir:
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
            base=repo_branch,
            head=new_branch_name,
            body=text,
    )

    if merge_policy is MergePolicy.MANUAL:
        return

    # auto-merge - todo: make configurable (e.g. merge method)
    pull_request.merge()
    rm_pr_branch()

    if after_merge_callback:
        subprocess.run(
            [os.path.join(repo_dir, after_merge_callback)],
            check=True,
            env=cmd_env
        )


UpstreamUpdatePolicy = concourse.model.traits.update_component_deps.UpstreamUpdatePolicy


def determine_reference_versions(
        component_name: str,
        reference_version: str,
        component_resolver: product.util.ComponentResolver,
        component_descriptor_resolver: product.util.ComponentDescriptorResolver,
        upstream_component_name: str=None,
        upstream_update_policy: UpstreamUpdatePolicy=UpstreamUpdatePolicy.STRICTLY_FOLLOW,
        _component: callable=_component, # allow easier mocking (for unittests)
        upstream_reference_component: callable=upstream_reference_component, # allow easier mocking
) -> typing.Sequence[str]:
    if upstream_component_name is None:
        # no upstream component defined - look for greatest released version
        return (component_resolver.latest_component_version(component_name),)

    version_candidate = _component(
        upstream_reference_component(
          component_resolver=component_resolver,
          component_descriptor_resolver=component_descriptor_resolver,
          component_name=upstream_component_name,
        ).dependencies(), component_name).version()
    version_candidate = version.parse_to_semver(version_candidate)
    if upstream_update_policy is UpstreamUpdatePolicy.STRICTLY_FOLLOW:
        return (str(version_candidate),)
    elif upstream_update_policy is UpstreamUpdatePolicy.ACCEPT_HOTFIXES:
        pass # continue
    else:
        raise NotImplementedError

    # also consider hotfixes
    hotfix_candidate = component_resolver.greatest_component_version_with_matching_minor(
      component_name=component_name,
      reference_version=str(reference_version),
    )
    hotfix_candidate = version.parse_to_semver(hotfix_candidate)
    return (str(hotfix_candidate), str(version_candidate))
