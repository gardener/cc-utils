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
import github3.exceptions

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
    component_version: str,
    upgrade_requests,
):
    return any(
        [
            upgrade_rq.target_matches(
                reference=component_reference,
                reference_version=component_version,
            )
            for upgrade_rq in upgrade_requests
        ]
    )


def latest_component_version_from_upstream(
    component_name: str,
    upstream_component_name: str,
    base_url: str,
):
    upstream_component_version = product.v2.latest_component_version(
        component_name=upstream_component_name,
        ctx_repo_base_url=base_url,
    )

    if not upstream_component_version:
        raise RuntimeError(
            f'did not find any versions for {upstream_component_name=}, {base_url=}'
        )

    upstream_component_descriptor = product.v2.download_component_descriptor_v2(
        component_name=upstream_component_name,
        component_version=upstream_component_version,
        ctx_repo_base_url=base_url,
    )
    upstream_component = upstream_component_descriptor.component
    for component_ref in upstream_component.componentReferences:
        # TODO: Validate that component_name is unique
        if component_ref.name == component_name:
            return component_ref.version


def determine_reference_versions(
    component_name: str,
    reference_version: str,
    repository_ctx_base_url: str,
    upstream_component_name: str=None,
    upstream_update_policy: UpstreamUpdatePolicy=UpstreamUpdatePolicy.STRICTLY_FOLLOW,
) -> typing.Sequence[str]:
    if upstream_component_name is None:
        # no upstream component defined - look for greatest released version
        latest_component_version = product.v2.latest_component_version(
                component_name=component_name,
                ctx_repo_base_url=repository_ctx_base_url,
        )
        if not latest_component_version:
            raise RuntimeError(
                f'did not find any versions of {component_name=} {repository_ctx_base_url=}'
            )

        return (
            latest_component_version,
        )

    version_candidate = latest_component_version_from_upstream(
        component_name=component_name,
        upstream_component_name=upstream_component_name,
        base_url=repository_ctx_base_url,
    )

    if upstream_update_policy is UpstreamUpdatePolicy.STRICTLY_FOLLOW:
        return (version_candidate,)

    elif upstream_update_policy is UpstreamUpdatePolicy.ACCEPT_HOTFIXES:
        hotfix_candidate = product.v2.greatest_component_version_with_matching_minor(
            component_name=component_name,
            ctx_repo_base_url=repository_ctx_base_url,
            reference_version=reference_version,
        )
        return (hotfix_candidate, version_candidate)

    else:
        raise NotImplementedError


def determine_upgrade_prs(
    upstream_component_name: str,
    upstream_update_policy: UpstreamUpdatePolicy,
    upgrade_pull_requests,
    ctx_repo_base_url: str,
) -> typing.Iterable[typing.Tuple[
    gci.componentmodel.ComponentReference, gci.componentmodel.ComponentReference, str
]]:
    for greatest_component_reference in product.v2.greatest_references(
        references=current_component().componentReferences,
    ):
        for greatest_version in determine_reference_versions(
            component_name=greatest_component_reference.componentName,
            reference_version=greatest_component_reference.version,
            upstream_component_name=upstream_component_name,
            upstream_update_policy=upstream_update_policy,
            repository_ctx_base_url=ctx_repo_base_url,
        ):
            if not greatest_version:
                # if None is returned, no versions at all were found
                print(
                    'Warning: no component versions found for '
                    f'{greatest_component_reference.componentName=}'
                )
                continue

            greatest_version_semver = version.parse_to_semver(greatest_version)
            print(f'{greatest_version=}, ours: {greatest_component_reference} {ctx_repo_base_url=}')
            if greatest_version_semver <= version.parse_to_semver(
                greatest_component_reference.version
            ):
                ci.util.info(
                    'skipping outdated component upgrade: '
                    f'{greatest_component_reference.componentName}; '
                    f'our version: {greatest_component_reference.version}, '
                    f'found: {greatest_version}'
                )
                continue
            elif upgrade_pr_exists(
                component_reference=greatest_component_reference,
                component_version=greatest_version,
                upgrade_requests=upgrade_pull_requests,
            ):
                ci.util.info(
                    'skipping upgrade (PR already exists): '
                    f'{greatest_component_reference.componentName} '
                    f'to version {greatest_version}'
                )
                continue
            else:
                yield(greatest_component_reference, greatest_version)


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

    # prepare env for upgrade script and after-merge-callback
    cmd_env = os.environ.copy()
    # TODO: Handle upgrades for types other than 'component'
    cmd_env['DEPENDENCY_TYPE'] = product.v2.COMPONENT_TYPE_NAME
    cmd_env['DEPENDENCY_NAME'] = to_ref.componentName
    cmd_env['LOCAL_DEPENDENCY_NAME'] = to_ref.name
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
    repo_owner = githubrepobranch.repo_owner
    repo_name = githubrepobranch.repo_name
    github_cfg = cfg_factory.github(github_cfg_name)

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
    try:
        ls_repo.ref(f'heads/{upgrade_branch_name}').delete()
    except github3.exceptions.NotFoundError:
        pass

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
