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

import gci.componentmodel

import ccc.github
import ci.util
import concourse.model.traits.update_component_deps
import concourse.steps.component_descriptor_util as cdu
import gitutil
import github.util
import product.model
import product.util
import product.v2
import version
from concourse.model.traits.update_component_deps import (
    MergePolicy,
)
from github.util import (
    GitHubRepositoryHelper,
    GitHubRepoBranch,
)
from github.release_notes.util import ReleaseNotes


UpstreamUpdatePolicy = concourse.model.traits.update_component_deps.UpstreamUpdatePolicy


def current_product_descriptor():
    return gci.componentmodel.ComponentDescriptor.from_dict(
        component_descriptor_dict=ci.util.parse_yaml_file(
            os.path.join(
                ci.util.check_env('COMPONENT_DESCRIPTOR_DIR'),
                cdu.component_descriptor_fname(gci.componentmodel.SchemaVersion.V2),
            )
        )
    )


def current_component():
    return current_product_descriptor().component


def current_base_url():
    last_ctx_repo = current_component().repositoryContexts[-1]
    return last_ctx_repo.baseUrl


def component_by_ref_and_version(
    component_reference: gci.componentmodel.ComponentReference,
    component_version: str,
):
    component_descriptor = product.v2.retrieve_component_descriptor_from_oci_ref(
        product.v2._target_oci_ref(
            component=current_component(),
            component_ref=component_reference,
            component_version=component_version,
        )
    )
    return component_descriptor.component


def github_access_for_component(component, cfg_factory):
    component_source = ccc.github._get_single_repo(component)

    # existence of access is guaranteed by _get_single_repo
    repo_url = component_source.access.repoUrl
    host_name, org, repo_name = repo_url.split('/')
    github_cfg = ccc.github.github_cfg_for_hostname(
        host_name=host_name,
        cfg_factory=cfg_factory,
    )

    return (github_cfg, org, repo_name)


def close_obsolete_pull_requests(
    upgrade_pull_requests,
    reference_component: gci.componentmodel.Component,
):
    obsolete_upgrade_requests = [
        pr for pr in upgrade_pull_requests
        if pr.pull_request.state == 'open' and pr.is_obsolete(
            reference_component=reference_component,
        )
    ]

    for obsolete_request in obsolete_upgrade_requests:
        obsolete_request.purge()


def upgrade_pr_exists(
    component_reference: gci.componentmodel.ComponentReference,
    upgrade_requests,
):
    return any(
        [
            upgrade_rq.target_matches(reference=component_reference)
            for upgrade_rq in upgrade_requests
        ]
    )


def determine_reference_versions(
    component_name: str,
    reference_version: str,
    upstream_component_name: str=None,
    upstream_update_policy: UpstreamUpdatePolicy=UpstreamUpdatePolicy.STRICTLY_FOLLOW,
) -> typing.Sequence[str]:
    base_url = current_base_url()
    if upstream_component_name is None:
        # no upstream component defined - look for greatest released version
        return (product.v2.latest_component_version(component_name, base_url),)

    version_candidate = product.v2.latest_component_version(upstream_component_name, base_url)

    if upstream_update_policy is UpstreamUpdatePolicy.STRICTLY_FOLLOW:
        return (version_candidate,)

    elif upstream_update_policy is UpstreamUpdatePolicy.ACCEPT_HOTFIXES:
        hotfix_candidate = product.v2.greatest_component_version_with_matching_minor(
            component_name=component_name,
            reference_version=reference_version,
        )
        return (hotfix_candidate, version_candidate)

    else:
        raise NotImplementedError


def determine_upgrade_prs(
    upstream_component_name: str,
    upstream_update_policy: UpstreamUpdatePolicy,
    upgrade_pull_requests,
) -> typing.Iterable[typing.Tuple[
    gci.componentmodel.ComponentReference, gci.componentmodel.ComponentReference, str
]]:
    for greatest_component_reference in product.v2.greatest_references(
        references=current_component().componentReferences,
    ):
        for latest_version in determine_reference_versions(
            component_name=greatest_component_reference.name,
            reference_version=greatest_component_reference.version,
            upstream_component_name=upstream_component_name,
            upstream_update_policy=upstream_update_policy,
        ):
            latest_version_semver = version.parse_to_semver(latest_version)
            print(f'latest_version: {latest_version}, ref: {greatest_component_reference}')
            if latest_version_semver <= version.parse_to_semver(
                greatest_component_reference.version
            ):
                ci.util.info(
                    f'skipping outdated component upgrade: {greatest_component_reference.name}; '
                    f'our version: {greatest_component_reference.version}, '
                    f'found: {latest_version}'
                )
                continue
            elif upgrade_pr_exists(
                component_reference=greatest_component_reference,
                upgrade_requests=upgrade_pull_requests,
            ):
                ci.util.info(
                    f'skipping upgrade (PR already exists): {greatest_component_reference.name}'
                )
                continue
            else:
                yield(greatest_component_reference, latest_version)


def create_upgrade_pr(
    from_ref: gci.componentmodel.ComponentReference,
    to_ref: gci.componentmodel.ComponentReference,
    to_version: str,
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
    from_version = from_ref.version
    from_component = component_by_ref_and_version(
        component_reference=from_ref,
        component_version=from_version,
    )

    # prepare env for upgrade script and after-merge-callback
    cmd_env = os.environ.copy()
    # TODO: Handle upgrades for types other than 'component'
    cmd_env['DEPENDENCY_TYPE'] = product.v2.COMPONENT_TYPE_NAME
    cmd_env['DEPENDENCY_NAME'] = to_ref.name
    cmd_env['DEPENDENCY_VERSION'] = to_version
    cmd_env['REPO_DIR'] = repo_dir
    cmd_env['GITHUB_CFG_NAME'] = github_cfg_name

    # create upgrade diff
    subprocess.run(
        [str(upgrade_script_path)],
        check=True,
        env=cmd_env
    )

    commit_message = f'Upgrade {to_ref.name}\n\nfrom {from_version} to {to_version}'

    upgrade_branch_name = push_upgrade_commit(
        ls_repo=ls_repo,
        commit_message=commit_message,
        githubrepobranch=githubrepobranch,
        repo_dir=repo_dir,
    )

    github_cfg, repo_owner, repo_name = github_access_for_component(from_component, cfg_factory)
    release_notes = create_release_notes(
        from_github_cfg=github_cfg,
        from_repo_owner=repo_owner,
        from_repo_name=repo_name,
        from_version=from_version,
        to_version=to_version,
    )

    if not release_notes:
        release_notes = pull_request_util.retrieve_pr_template_text()

    pull_request = ls_repo.create_pull(
        title=github.util.PullRequestUtil.calculate_pr_title(
            reference=to_ref,
            from_version=from_version,
            to_version=to_version
        ),
        base=githubrepobranch.branch(),
        head=upgrade_branch_name,
        body=release_notes,
    )

    if merge_policy is MergePolicy.MANUAL:
        return
    # auto-merge - todo: make configurable (e.g. merge method)
    pull_request.merge()
    ls_repo.ref(f'heads/{upgrade_branch_name}').delete()

    if after_merge_callback:
        subprocess.run(
            [os.path.join(repo_dir, after_merge_callback)],
            check=True,
            env=cmd_env
        )


def push_upgrade_commit(
    ls_repo,
    commit_message: str,
    githubrepobranch,
    repo_dir: str,
) -> str:
    # mv diff into commit and push it
    helper = gitutil.GitHelper.from_githubrepobranch(
        githubrepobranch=githubrepobranch,
        repo_path=repo_dir,
    )
    commit = helper.index_to_commit(message=commit_message)
    ci.util.info(f'commit for upgrade-PR: {commit.hexsha}')
    new_branch_name = ci.util.random_str(prefix='ci-', length=12)
    repo_branch = githubrepobranch.branch()
    head_sha = ls_repo.ref(f'heads/{repo_branch}').object.sha
    ls_repo.create_ref(f'refs/heads/{new_branch_name}', head_sha)

    try:
        helper.push(from_ref=commit.hexsha, to_ref=f'refs/heads/{new_branch_name}')
    except:
        ci.util.warning('an error occurred - removing now useless pr-branch')
        ls_repo.ref(f'heads/{new_branch_name}').delete()
        raise

    helper.repo.git.checkout('.')

    return new_branch_name


def create_release_notes(
    from_github_cfg,
    from_repo_owner: str,
    from_repo_name: str,
    from_version: str,
    to_version: str,
):
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            from_github_helper = GitHubRepositoryHelper(
                github_cfg=from_github_cfg,
                owner=from_repo_owner,
                name=from_repo_name,
            )
            from_git_helper = gitutil.GitHelper.clone_into(
                target_directory=temp_dir,
                github_cfg=from_github_cfg,
                github_repo_path=f'{from_repo_owner}/{from_repo_name}'
            )
            commit_range = '{from_version}..{to_version}'.format(
                from_version=from_version,
                to_version=to_version,
            )
            release_note_blocks = ReleaseNotes.create(
                github_helper=from_github_helper,
                git_helper=from_git_helper,
                commit_range=commit_range
            ).release_note_blocks()
            if release_note_blocks:
                return f'**Release Notes*:\n{release_note_blocks}'
    except:
        ci.util.warning('an error occurred during release notes processing (ignoring)')
        import traceback
        ci.util.warning(traceback.format_exc())

    return None
