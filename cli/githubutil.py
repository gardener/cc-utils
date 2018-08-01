# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

import version
from urllib.parse import urlparse, parse_qs
from github3.exceptions import NotFoundError

from util import ctx, not_empty, info, warning, fail, verbose, CliHint, CliHints, existing_dir
from github.webhook import GithubWebHookSyncer, WebhookQueryAttributes
from github.util import (
    GitHubRepositoryHelper,
    _create_github_api_object,
    _create_team,
    _add_user_to_team,
    _add_all_repos_to_team
)
import product.model
from github.release_notes import generate_release_notes, get_release_note_blocks



def assign_github_team_to_repo(
    github_cfg_name: str,
    github_org_name: str,
    auth_token: CliHint(help="Token from an org admin user. Token must have 'admin:org' scope"),
    team_name: str='ci'
):
    '''
    Assign team 'team_name' to all repositories in organization 'github_org_name' and
    give the team admin rights on those repositories. The team will be created if it does not exist
    and the technical github user (from github_cfg_name) will be assigned to the team.
    The token of the technical github user must have the privilege to create webhooks (scope admin:repo_hook)
    The 'auth_token' parameter must belong to an org admin. The token must have 'admin:org' privileges.
    '''
    cfg_factory = ctx().cfg_factory()
    github_cfg = cfg_factory.github(github_cfg_name)
    github_username = github_cfg.credentials().username()

    # overwrite auth_token
    github_cfg.credentials().set_auth_token(auth_token=auth_token)

    github = _create_github_api_object(
        github_cfg=github_cfg,
    )

    _create_team(
        github=github,
        organization_name=github_org_name,
        team_name=team_name
    )

    _add_user_to_team(
        github=github,
        organization_name=github_org_name,
        team_name=team_name,
        user_name=github_username
    )

    _add_all_repos_to_team(
        github=github,
        organization_name=github_org_name,
        team_name=team_name
    )

def generate_release_notes_cli(
    repo_dir: str,
    github_cfg_name: str,
    github_repository_owner: str,
    github_repository_name: str,
    repository_branch: str,
    commit_range: str=None
):
    github_cfg = ctx().cfg_factory().github(github_cfg_name)
    helper = GitHubRepositoryHelper(
        github_cfg=github_cfg,
        owner=github_repository_owner,
        name=github_repository_name,
        default_branch=repository_branch,
    )
    generate_release_notes(repo_dir=repo_dir, helper=helper, repository_branch=repository_branch, commit_range=commit_range)

def release_and_prepare_next_dev_cycle(
    github_cfg_name: str,
    github_repository_owner: str,
    github_repository_name: str,
    repository_branch: str,
    repository_version_file_path: str,
    release_version: str,
    version_operation: str="bump_minor",
    prerelease_suffix: str="dev",
    author_name: str="gardener-ci",
    author_email: str="gardener.ci.user@gmail.com",
    component_descriptor_file_path: str=None,
    should_generate_release_notes: bool=True,
    repo_dir: str=None
):
    github_cfg = ctx().cfg_factory().github(github_cfg_name)

    helper = GitHubRepositoryHelper(
        github_cfg=github_cfg,
        owner=github_repository_owner,
        name=github_repository_name,
        default_branch=repository_branch,
    )

    if helper.tag_exists(tag_name=release_version):
        fail(
            "Cannot create tag '{t}' in preparation for release: Tag already exists in the repository.".format(
                t=release_version,
            )
        )

    if should_generate_release_notes:
        release_notes = generate_release_notes(
            repo_dir=repo_dir,
            helper=helper,
            repository_branch=repository_branch
        )
    else:
        release_notes = 'release notes'

    # Do all the version handling upfront to catch errors early
    # Bump release version and add suffix
    next_version = version.process_version(
        version_str=release_version,
        operation=version_operation
    )
    next_version_dev = version.process_version(
        version_str=next_version,
        operation='set_prerelease',
        prerelease=prerelease_suffix
    )

    draft_name = draft_release_name_for_version(release_version)
    draft_release = helper.draft_release_with_name(draft_name)
    if draft_release:
        verbose('cleaning up draft release {name}'.format(name=draft_release.name))
        draft_release.delete()

    # Persist version change, create release commit
    release_commit_sha = helper.create_or_update_file(
        file_path=repository_version_file_path,
        file_contents=release_version,
        commit_message="Release " + release_version
    )
    helper.create_tag(
        tag_name=release_version,
        tag_message="Release " + release_version,
        repository_reference=release_commit_sha,
        author_name=author_name,
        author_email=author_email
    )
    release = helper.create_release(
        tag_name=release_version,
        body=release_notes,
        draft=False,
        prerelease=False
    )

    if component_descriptor_file_path:
        with open(component_descriptor_file_path) as f:
            # todo: validate descriptor
            component_descriptor_contents = f.read()
        release.upload_asset(
            content_type='application/x-yaml',
            name=product.model.COMPONENT_DESCRIPTOR_ASSET_NAME,
            asset=component_descriptor_contents,
            label=product.model.COMPONENT_DESCRIPTOR_ASSET_NAME,
        )

    # Prepare version file for next dev cycle
    helper.create_or_update_file(
        file_path=repository_version_file_path,
        file_contents=next_version_dev,
        commit_message="Prepare next dev cycle " + next_version_dev
    )

def draft_release_name_for_version(release_version: str):
    return "{v}-draft".format(v=release_version)

def create_or_update_draft_release(
    github_cfg_name: str,
    github_repository_owner: str,
    github_repository_name: str,
    repository_branch: str,
    release_version: str,
    repo_dir: str=None
):
    github_cfg = ctx().cfg_factory().github(github_cfg_name)

    helper = GitHubRepositoryHelper(
        github_cfg=github_cfg,
        owner=github_repository_owner,
        name=github_repository_name,
        default_branch=repository_branch,
    )

    release_notes = generate_release_notes(
        repo_dir=repo_dir,
        helper=helper,
        repository_branch=repository_branch
    )

    draft_name = draft_release_name_for_version(release_version)
    draft_release = helper.draft_release_with_name(draft_name)
    if not draft_release:
        release = helper.create_release(
            tag_name='',
            name=draft_name,
            body=release_notes,
            draft=True,
            prerelease=False
        )
    else:
        if not draft_release.body == release_notes:
            draft_release.edit(body=release_notes)
        else:
            info('draft release notes are already up to date')

def get_release_note_blocks_cli(
    repo_dir: str,
    github_cfg_name: str,
    github_repository_owner: str,
    github_repository_name: str,
    repository_branch: str,
    commit_range: str=None
):
    github_cfg = ctx().cfg_factory().github(github_cfg_name)

    helper = GitHubRepositoryHelper(
        github_cfg=github_cfg,
        owner=github_repository_owner,
        name=github_repository_name,
        default_branch=repository_branch,
    )

    get_release_note_blocks(
        repo_dir=repo_dir,
        helper=helper,
        repository_branch=repository_branch,
        commit_range=commit_range
    )

def remove_webhooks(
    github_org_name: CliHints.non_empty_string(
        help='process all repositories in the given github organisation'
    ),
    github_cfg_name: CliHints.non_empty_string(
        help='github_cfg name (see cc-config repo)'
    ),
    concourse_cfg_name: CliHints.non_empty_string(
        help='the concourse_cfg name for which webhooks are to be removed'
    ),
    job_mapping_name: CliHint(help='the name of the job mapping whose webhooks are to be removed') = None,
):
    '''
    Remove all webhooks which belong to the given Concourse-config name. If a job-mapping id is given as well,
    only webhooks tagged with both Concourse-config name and job-mapping id will be deleted.
    '''
    cfg_factory = ctx().cfg_factory()
    github_cfg = cfg_factory.github(github_cfg_name)

    github_api = _create_github_api_object(github_cfg=github_cfg)
    github_org = github_api.organization(github_org_name)
    webhook_syncer = GithubWebHookSyncer(github_api)

    def filter_function(url):
        parsed_url = parse_qs(urlparse(url).query)
        concourse_id = parsed_url.get(WebhookQueryAttributes.CONCOURSE_ID_ATTRIBUTE_NAME)
        job_mapping_id = parsed_url.get(WebhookQueryAttributes.JOB_MAPPING_ID_ATTRIBUTE_NAME)
        job_id_matches_or_absent = job_mapping_id is None or job_mapping_name in job_mapping_id
        concourse_id_matches = concourse_id is not None and concourse_cfg_name in concourse_id

        should_delete = job_id_matches_or_absent and concourse_id_matches
        return should_delete

    for repository in github_org.repositories():
        removed = 0
        try:
            _, removed = webhook_syncer.remove_outdated_hooks(
                owner=github_org_name,
                repository_name=repository.name,
                urls_to_keep=[],
                url_filter_fun=filter_function
            )
        except NotFoundError as err:
            warning("{msg}. Please check privileges for repository {repo}".format(
                msg=err,
                repo=repository.name)
            )
            continue

        if removed > 0:
            info("Removed {num} webhook from repository {repo}".format(num=removed, repo=repository.name))
        else:
            verbose("Nothing to do for repository {repo}".format(repo=repository.name))
