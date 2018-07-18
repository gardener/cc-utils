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
from pydash import _
from collections import namedtuple
from semver import parse_version_info
import git
import re

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

ReleaseNote = namedtuple('ReleaseNote', ["category_id", "group_id", "text", "pr_number", "user_login"])
Category = namedtuple("Category", "identifier title")
Group = namedtuple("Group", "identifier title")
categories = \
    Category(identifier='noteworthy', title='## Most notable changes'),\
    Category(identifier='improvement', title='## Improvements')
groups = \
    Group(identifier='user', title='### To end users'), \
    Group(identifier='operator', title='### To operations team')

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

def generate_release_notes(
    repo_dir: str,
    helper: GitHubRepositoryHelper,
    repository_branch: str,
    commit_range: str=None
):
    repo = git.Repo(existing_dir(repo_dir))
    if repo.active_branch.name != repository_branch:
        fail('need to switch to branch {branch}. Current active branch: {active_branch}'.format(branch=repository_branch, active_branch=repo.active_branch.name))

    if not commit_range:
        commit_range = calculate_range(repo, helper)
    pr_numbers = fetch_pr_numbers_in_range(repo, commit_range)
    release_note_objs = fetch_release_notes_from_prs(helper, pr_numbers)
    release_notes = build_markdown(release_note_objs)

    info(release_notes)
    return release_notes

def build_markdown(
    release_note_objs: list
) -> str:
    def release_notes_for_group(
        category: Category,
        group: Group,
        release_note_objs: list()
    ) -> list:
        release_note_objs = _.filter(release_note_objs, lambda release_note: category.identifier == release_note.category_id and group.identifier == release_note.group_id)

        release_note_lines = list()
        if release_note_objs:
            release_note_lines.append(group.title)
            for release_note in release_note_objs:
                for i, rls_note_line in enumerate(release_note.text.splitlines()):
                    if i == 0:
                        release_note_lines.append('* {rls_note_line} (#{pr_num}, @{user})'.format(pr_num=release_note.pr_number, user=release_note.user_login, rls_note_line=rls_note_line))
                    else:
                        release_note_lines.append('  * {rls_note_line}'.format(rls_note_line=rls_note_line))
        return release_note_lines

    def release_notes_for_category(
        category: Category,
        release_note_objs: list
    ) -> list:
        rn_lines = list()
        rn_lines_category = list()
        for group in groups:
            rn_lines_category.extend(release_notes_for_group(category=category, group=group, release_note_objs=release_note_objs))

        if rn_lines_category:
            rn_lines.append(category.title)
            rn_lines.extend(rn_lines_category)
        return rn_lines

    release_note_lines = list()
    for category in categories:
        release_note_lines.extend(release_notes_for_category(category, release_note_objs=release_note_objs))

    if release_note_lines:
        return '\n'.join(release_note_lines)
    else: # fallback
        return 'release notes'

def calculate_range(
    repo: git.Repo,
    helper: GitHubRepositoryHelper,
) -> str:

    branch_head = repo.head.commit
    range_start = _.head(reachable_release_tags_from_commit(helper, repo, branch_head))

    range_end = None
    try:
        range_end = repo.git.describe(branch_head) # better readable range_end by describing head commit
    except git.exc.GitCommandError:
        range_end = branch_head.hexsha

    commit_range = "{start}..{end}".format(start=range_start, end=range_end)
    return commit_range

def release_tags(
    helper: GitHubRepositoryHelper,
    repo: git.Repo
) -> list:
    release_tags = helper.release_tags()
    tags = _ \
        .chain(repo.tags) \
        .map(lambda tag: {"tag": tag.name, "commit": tag.commit.hexsha}) \
        .filter(lambda item: _.find(release_tags, lambda el: el == item['tag'])) \
        .key_by('commit') \
        .map_values('tag') \
        .value()
    return tags

def reachable_release_tags_from_commit(
    helper: GitHubRepositoryHelper,
    repo: git.Repo, commit
) -> list:
    tags = release_tags(helper, repo)

    visited = set()
    queue = list()
    queue.append(commit)
    visited.add(commit.hexsha)

    reachable_tags = list()

    while queue:
        commit = queue.pop(0)
        if commit.hexsha in tags:
            reachable_tags.append(tags[commit.hexsha])
        not_visited_parents = _.filter(commit.parents, lambda parent_commit: not parent_commit.hexsha in visited)
        if not_visited_parents:
            queue.extend(not_visited_parents)
            # queue.sort(key=lambda commit: commit.committed_date, reverse=True) #not needed anymore as we sort by semver at the end
            visited |= set(_.map(not_visited_parents, lambda commit: commit.hexsha))

    reachable_tags.sort(key=lambda t: parse_version_info(t), reverse=True)

    if not reachable_tags:
        warning('no release tag found, falling back to root commit')
        root_commits = repo.iter_commits(rev=commit, max_parents=0)
        root_commit = next(root_commits, None)
        if not root_commit:
            fail('could not determine root commit from rev {rev}'.format(rev=commit.hexsha))
        if next(root_commits, None):
            fail('cannot determine range for release notes. Repository has multiple root commits. Specify range via commit_range parameter.')
        reachable_tags.append(root_commit.hexsha)

    return reachable_tags


def fetch_pr_numbers_in_range(
    repo: git.Repo,
    commit_range: str
) -> set:
    info('git log {range}'.format(range=commit_range))
    gitLogs = repo.git.log(commit_range, pretty='%s').splitlines()
    pr_numbers = []
    for commitMessage in gitLogs:
        if commitMessage.startswith('Merge pull'):
            pr_number = _.head(re.findall(r"#(\d+|$)", commitMessage))
            if pr_number:
                pr_numbers.append(pr_number)

    verbose('Merged pull request numbers in range {range}: {pr_numbers}'.format(range=commit_range, pr_numbers=pr_numbers))
    return pr_numbers

def fetch_release_notes_from_prs(
    helper: GitHubRepositoryHelper,
    pr_numbers_in_range: set
) -> list:
    # we should consider adding a release-note label to the PRs to reduce the number of search results
    prs_iter = helper.search_issues_in_repo('type:pull is:closed')

    release_notes = list()
    for pr_iter in prs_iter:
        pr_dict = pr_iter.as_dict()

        pr_number = pr_dict['number']
        if not str(pr_number) in pr_numbers_in_range:
            continue

        release_notes_pr = extract_release_notes(pr_number=pr_number, text=pr_dict['body'], user_login=_.get(pr_dict, 'user.login'))
        if not release_notes_pr:
            continue

        release_notes.extend(release_notes_pr)
    return release_notes

def extract_release_notes(
    pr_number: int,
    text: str,
    user_login: str
) -> list:
    release_notes = list()

    quotes = re.findall(r"``` *(improvement|noteworthy)( (user|operator)?)?.*?\n(.*?)\n```", text, re.MULTILINE | re.DOTALL)
    for quote in quotes:
        quote = _.map(quote, lambda obj: _.trim(obj))

        text = quote[3]
        if not text or 'none' == text.lower():
            continue

        category = quote[0]
        group = quote[2] or 'user'

        release_notes.append(ReleaseNote(category_id=category, group_id=group, text=text, pr_number=pr_number, user_login=user_login))
    return release_notes

def release_and_prepare_next_dev_cycle(
    github_cfg_name: str,
    github_repository_owner: str,
    github_repository_name: str,
    repository_branch: str,
    repository_version_file_path: str,
    release_version: str,
    release_notes: str=None,
    version_operation: str="bump_minor",
    prerelease_suffix: str="dev",
    author_name: str="gardener-ci",
    author_email: str="gardener.ci.user@gmail.com",
    component_descriptor_file_path: str=None,
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

    if not release_notes:
        release_notes = generate_release_notes(repo_dir=repo_dir, helper=helper, repository_branch=repository_branch, commit_range=commit_range)

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
