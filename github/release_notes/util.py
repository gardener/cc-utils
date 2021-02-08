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

import re
import typing

import git
import requests

import ccc
import cnudie.retrieve
import cnudie.util
import gci.componentmodel
import product.v2
import version

from git.exc import GitError
from github.util import GitHubRepositoryHelper
from pydash import _

from ci.util import info, warning, fail, verbose, ctx
from github.release_notes.model import (
    REF_TYPE_PULL_REQUEST, ReleaseNote,
    Commit,
    ReleaseNoteBlock,
    ReferenceType,
    reference_type_for_type_identifier,
    REF_TYPE_COMMIT
)
from github.release_notes.renderer import (
    MarkdownRenderer,
    CATEGORIES,
    TARGET_GROUPS
)
from gitutil import GitHelper
from model.base import ModelValidationError
from slackclient.util import SlackHelper


def fetch_release_notes(
    component: gci.componentmodel.Component,
    repository_branch: str,
    repo_dir: str,
):
    release_notes = ReleaseNotes(component, repo_dir)
    release_notes.create(start_ref=repository_branch)
    return release_notes


def post_to_slack(
    release_notes: ReleaseNote,
    github_repository_name: str,
    slack_cfg_name: str,
    slack_channel: str,
    release_version: str,
    max_msg_size_bytes: int=20000,
):
    # slack can't auto link pull requests, commits or users
    # hence we force the link generation when building the markdown string
    release_notes_md_links = release_notes.to_markdown(
        force_link_generation=True
    )

    # XXX slack imposes a maximum msg size
    # https://api.slack.com/changelog/2018-04-truncating-really-long-messages#

    slack_cfg = ctx().cfg_factory().slack(slack_cfg_name)
    slack_helper = SlackHelper(slack_cfg)

    idx = 0
    i = 0

    try:
        while True:
            title = f'[{github_repository_name}:{release_version} released'

            # abort on last
            if idx + max_msg_size_bytes > len(release_notes_md_links):
                did_split = i > 0
                if did_split:
                    title += ' - final]'
                else:
                    title += ']'

                msg = release_notes_md_links[idx:]
                yield slack_helper.post_to_slack(channel=slack_channel, title=title, message=msg)
                break

            # post part
            title += f' - part {i} ]'
            msg = release_notes_md_links[idx: idx+max_msg_size_bytes]
            yield slack_helper.post_to_slack(channel=slack_channel, title=title, message=msg)

            i += 1
            idx += max_msg_size_bytes

    except RuntimeError as e:
        warning(e)


def delete_file_from_slack(
    slack_cfg_name: str,
    file_id: str,
):
    slack_cfg = ctx().cfg_factory().slack(slack_cfg_name)
    response = SlackHelper(slack_cfg).delete_file(
        file_id=file_id,
    )
    return response


def github_repo_path(owner, name):
    return owner + '/' + name


class ReleaseNotes:
    def __init__(
        self,
        component: gci.componentmodel.Component=None,
        repo_dir: str=None,
    ):
        if component:
            self.component = component
            source = cnudie.util.determine_main_source_for_component(component)
            if repo_dir:
                self.github_helper = github_helper_from_github_access(
                    github_access=source.access,
                )
                self.git_helper = git_helper_from_github_access(
                    github_access=source.access,
                    repo_path=repo_dir,
                )

    def create(
        self,
        start_ref: str,
        commit_range: str=None,
    ):
        self.release_note_objs = self._rls_note_objs(
            repository_branch=start_ref,
            commit_range=commit_range,
        )

    def to_markdown(
        self,
        force_link_generation: bool=False
    ) -> str:
        release_notes_str = MarkdownRenderer(
            release_note_objs=self.release_note_objs,
            force_link_generation=force_link_generation
        ).render()

        info('Release notes:\n{rn}'.format(rn=release_notes_str))
        return release_notes_str

    def release_note_blocks(self):
        block_strings = _.map(
            self.release_note_objs,
            lambda rls_note_obj: rls_note_obj.to_block_str()
        )

        if block_strings:
            release_notes_str = '\n\n'.join(block_strings)
        else:
            release_notes_str = ''

        info('Release note blocks:\n{rn}'.format(rn=release_notes_str))
        return release_notes_str

    def _rls_note_objs(
        self,
        repository_branch: str=None,
        commit_range: str=None,
    ) -> typing.List[ReleaseNote]:
        if not commit_range:
            commit_range = self.calculate_range(
                repository_branch,
            )
        info(f'Fetching release notes from revision range: {commit_range}')
        commits = self.commits_in_range(
            commit_range=commit_range,
            repository_branch=repository_branch,
        )
        pr_numbers = fetch_pr_numbers_from_commits(commits=commits)
        verbose(f'Merged pull request numbers in range {commit_range}: {pr_numbers}')
        release_note_objs = self.fetch_release_notes_from_prs(
            pr_numbers_in_range=pr_numbers,
        )
        release_note_objs.extend(
            fetch_release_notes_from_commits(
                commits=commits,
                current_component=self.component,
            )
        )

        return release_note_objs

    def calculate_range(
        self,
        repository_branch: str,
    ) -> str:
        repo = self.git_helper.repo
        branch_head = self.git_helper.fetch_head(ref=repository_branch)
        if not branch_head:
            fail(f'could not determine branch head of {repository_branch} branch')
        range_start = _.head(
            self.reachable_release_tags_from_commit(
                repo=repo,
                commit=branch_head,
            ),
        )

        try:
            # better readable range_end by describing head commit
            range_end = repo.git.describe(branch_head, tags=True)
        except GitError as err:
            warning(
                'failed to describe branch head, maybe the repository has no tags? '
                f'GitError: {err}. Falling back to branch head commit hash.'
            )
            range_end = branch_head.hexsha

        commit_range = f'{range_start}..{range_end}'
        return commit_range

    def release_tags(
        self,
    ) -> typing.List[str]:

        def is_valid_semver(tag_name):
            try:
                version.parse_to_semver(tag_name)
                return True
            except ValueError:
                warning('{tag} is not a valid SemVer string'.format(tag=tag_name))
                return False

        release_tags = self.github_helper.release_tags()
        tags = _ \
            .chain(self.git_helper.repo.tags) \
            .map(lambda tag: {"tag": tag.name, "commit": tag.commit.hexsha}) \
            .filter(lambda item: _.find(release_tags, lambda el: el == item['tag'])) \
            .filter(lambda item: is_valid_semver(item['tag'])) \
            .key_by('commit') \
            .map_values('tag') \
            .value()
        return tags

    def reachable_release_tags_from_commit(
        self,
        repo: git.Repo,
        commit: git.objects.Commit
    ) -> typing.List[str]:
        '''Returns a list of release-tags whose tagged commits are ancestors of the given commit.

        The returned list is sorted in descending order, putting the greatest reachable tag first.
        '''
        tags = self.release_tags()

        visited = set()
        queue = list()
        queue.append(commit)
        visited.add(commit.hexsha)

        reachable_tags = list()

        while queue:
            commit = queue.pop(0)
            if commit.hexsha in tags:
                reachable_tags.append(tags[commit.hexsha])
            not_visited_parents = _.filter(commit.parents,
                lambda parent_commit: parent_commit.hexsha not in visited
            )
            if not_visited_parents:
                queue.extend(not_visited_parents)
                visited |= set(_.map(not_visited_parents, lambda commit: commit.hexsha))

        reachable_tags.sort(key=lambda t: version.parse_to_semver(t), reverse=True)

        if not reachable_tags:
            warning('no release tag found, falling back to root commit')
            root_commits = repo.iter_commits(rev=commit, max_parents=0)
            root_commit = next(root_commits, None)
            if not root_commit:
                fail(f'could not determine root commit from rev {commit.hexsha}')
            if next(root_commits, None):
                fail(
                    'cannot determine range for release notes. Repository has multiple root '
                    'commits. Specify range via commit_range parameter.'
                )
            reachable_tags.append(root_commit.hexsha)

        return reachable_tags

    def commits_in_range(
        self,
        commit_range: str,
        repository_branch: str=None
    ) -> typing.List[Commit]:
        args = [commit_range]
        if repository_branch:
            args.append(repository_branch)

        GIT_FORMAT_KEYS = [
            "%H",  # commit hash
            "%s",  # subject
            "%B",  # raw body
        ]
        pretty_format = '%x00'.join(GIT_FORMAT_KEYS) # field separator
        pretty_format += '%x01' #line ending

        kwargs = {'pretty': pretty_format}
        git_logs = _.split(self.git_helper.repo.git.log(*args, **kwargs), '\x01')

        return commits_from_logs(git_logs)

    def fetch_release_notes_from_prs(
        self,
        pr_numbers_in_range: typing.Set[str],
    ) -> typing.List[ReleaseNote]:
        # we should consider adding a release-note label to the PRs
        # to reduce the number of search results
        prs_iter = self.github_helper.search_issues_in_repo('type:pull is:closed')

        release_notes = list()
        for pr_iter in prs_iter:
            pr_dict = pr_iter.as_dict()

            pr_number = str(pr_dict['number'])
            if pr_number not in pr_numbers_in_range:
                continue

            release_notes_pr = extract_release_notes(
                reference_id=pr_number,
                text=pr_dict['body'],
                user_login=_.get(pr_dict, 'user.login'),
                reference_type=REF_TYPE_PULL_REQUEST,
                current_component=self.component,
            )
            if not release_notes_pr:
                continue

            release_notes.extend(release_notes_pr)
        return release_notes


def fetch_release_notes_from_commits(
    current_component: gci.componentmodel.Component,
    commits: typing.List[Commit],
):
    release_notes = list()
    for commit in commits:
        release_notes_commit = extract_release_notes(
            reference_id=commit.hash,
            text=commit.message,
            user_login=None, # we do not have the gitHub user at hand
            reference_type=REF_TYPE_COMMIT,
            current_component=current_component,
        )
        if not release_notes_commit:
            continue

        release_notes.extend(release_notes_commit)
    return release_notes


def extract_release_notes(
    reference_type: ReferenceType,
    text: str,
    user_login: str,
    current_component: gci.componentmodel.Component,
    source_component=None,
    reference_id: str=None,
) -> typing.List[ReleaseNote]:
    """
    Keyword arguments:
    reference_type -- type of reference_id, either pull request or commit
    reference_id -- reference identifier, could be a pull request number or commit hash
    text -- release note text
    user_login -- github user_login, used for referencing the user
        in the release note via @<user_login>
    cn_current_repo -- component name of the current repository
    """
    release_notes = list()
    if not text:
        return release_notes

    CATEGORY_IDS = _ \
        .chain(CATEGORIES) \
        .map(lambda category: category.identifiers) \
        .flatten() \
        .join('|') \
        .value()

    TARGET_GROUP_IDS = _ \
        .chain(TARGET_GROUPS) \
        .map(lambda target_group: target_group.identifiers) \
        .flatten() \
        .join('|') \
        .value()

    r = re.compile(
        rf"``` *(?P<category>{CATEGORY_IDS}) (?P<target_group>{TARGET_GROUP_IDS})"
        r"( (?P<source_repo>\S+/\S+/\S+)(( (?P<reference_type>#|\$)(?P<reference_id>\S+))?"
        r"( @(?P<user>\S+))?)( .*?)?|( .*?)?)\r?\n(?P<text>.*?)\n```",
        re.MULTILINE | re.DOTALL
    )
    for m in r.finditer(text):
        code_block = m.groupdict()
        try:
            rls_note_block = create_release_note_block(
                code_block=code_block,
                reference_type=reference_type,
                reference_id=reference_id,
                user_login=user_login,
                current_component=current_component,
                source_component=source_component,
            )
            if not rls_note_block:
                continue
            release_notes.append(rls_note_block)
        except ModelValidationError as e:
            warning(f'an exception occurred while extracting release notes: {e}')
            continue
    return release_notes


def create_release_note_block(
    code_block: dict,
    reference_type: ReferenceType,
    user_login: str,
    current_component: gci.componentmodel.Component,
    source_component: gci.componentmodel.Component = None,
    reference_id: str=None,
) -> ReleaseNoteBlock:
    text = _.trim(code_block.get('text'))
    if not text or 'none' == text.lower():
        return None

    category = code_block.get('category')
    target_group = code_block.get('target_group')
    source_repo = code_block.get('source_repo')

    if source_component:
        reference_id = code_block.get('reference_id')
        reference_type = reference_type_for_type_identifier(code_block.get('reference_type'))
        user_login = code_block.get('user')
    elif source_repo:
        try:
            # try to fetch cd for the parsed source repo. The actual version does not matter,
            # we're only interested in the components GithubAccess (we assume it does not
            # change).
            ctx_repo_url = current_component.current_repository_ctx().baseUrl
            source_component = cnudie.retrieve.component_descriptor(
                name=source_repo,
                version=product.v2.greatest_component_version(
                    component_name=source_repo,
                    ctx_repo_base_url=ctx_repo_url,
                ),
                ctx_repo_url=ctx_repo_url,
            ).component
        except requests.exceptions.HTTPError:
            warning(f'Unable to retrieve component descriptor for source repository {source_repo}')
            return None

        reference_type = reference_type_for_type_identifier(code_block.get('reference_type'))
        reference_id = code_block.get('reference_id')
        user_login = code_block.get('user')
    else:
        source_component = current_component

    return ReleaseNoteBlock(
            category_id=category,
            target_group_id=target_group,
            text=text,
            reference_type=reference_type,
            reference_id=reference_id,
            user_login=user_login,
            source_component=source_component,
            current_component=current_component,
        )


def commits_from_logs(
    git_logs: typing.List[str]
) -> typing.List[Commit]:
    r = re.compile(
        r"(?P<commit_hash>\S+?)\x00(?P<commit_subject>.*)\x00(?P<commit_message>.*)",
        re.MULTILINE | re.DOTALL
    )
    commits = _\
        .chain(git_logs) \
        .map(lambda c: r.search(c)) \
        .filter(lambda m: m is not None) \
        .map(lambda m: m.groupdict()) \
        .map(lambda g: Commit(
            hash=g['commit_hash'],
            subject=g['commit_subject'],
            message=g['commit_message']
        )) \
        .value()
    return commits


def fetch_pr_numbers_from_commits(
    commits: typing.List[Commit]
) -> typing.Set[str]:
    pr_numbers = set()
    for commit in commits:
        pr_number = pr_number_from_subject(commit.subject)

        if pr_number:
            pr_numbers.add(pr_number)

    return pr_numbers


def pr_number_from_subject(commit_subject: str):
    pr_number = _.head(re.findall(r"Merge pull request #(\d+)", commit_subject))
    if not pr_number: # Squash commit
        pr_number = _.head(re.findall(r" \(#(\d+)\)$", commit_subject))
    return pr_number


def draft_release_name_for_version(release_version: str):
    return "{v}-draft".format(v=release_version)


def github_helper_from_github_access(
    github_access=gci.componentmodel.GithubAccess,
):
    return GitHubRepositoryHelper(
        github_api=ccc.github.github_api_from_gh_access(github_access),
        owner=github_access.org_name(),
        name=github_access.repository_name(),
    )


def git_helper_from_github_access(
    github_access: gci.componentmodel.GithubAccess,
    repo_path: str,
):
    return GitHelper(
        repo=repo_path,
        github_cfg=ccc.github.github_cfg_for_hostname(github_access.hostname()),
        github_repo_path=f'{github_access.org_name()}/{github_access.repository_name()}',
    )
