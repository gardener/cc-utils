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

from pydash import _
from collections import namedtuple
from semver import parse_version_info
import git
import re

from util import info, warning, fail, verbose, existing_dir
from github.util import GitHubRepositoryHelper

ReleaseNote = namedtuple('ReleaseNote', \
    ["category_id", "target_group_id", "text", "pr_number", "user_login"] \
)
Category = namedtuple("Category", ["identifier", "title"])
TargetGroup = namedtuple("TargetGroup", ["identifier", "title"])
categories = \
    Category(identifier='noteworthy', title='## Most notable changes'), \
    Category(identifier='improvement', title='## Improvements')
target_groups = \
    TargetGroup(identifier='user', title='### To end users'), \
    TargetGroup(identifier='operator', title='### To operations team')

def generate_release_notes(
    repo_dir: str,
    helper: GitHubRepositoryHelper,
    repository_branch: str,
    commit_range: str=None
):
    repo = git.Repo(existing_dir(repo_dir))

    if not commit_range:
        commit_range = calculate_range(repository_branch, repo, helper)
    pr_numbers = fetch_pr_numbers_in_range(repo, commit_range)
    release_note_objs = fetch_release_notes_from_prs(helper, pr_numbers)
    release_notes_str = build_markdown(release_note_objs)

    info(release_notes_str)
    return release_notes_str

def build_markdown(
    release_note_objs: list
) -> str:
    def release_notes_for_target_group(
        category: Category,
        target_group: TargetGroup,
        release_note_objs: list()
    ) -> list:
        release_note_objs = _.filter(
            release_note_objs,
            lambda release_note:
                category.identifier == release_note.category_id
                and target_group.identifier == release_note.target_group_id
        )

        release_note_lines = list()
        if release_note_objs:
            release_note_lines.append(target_group.title)
            for release_note in release_note_objs:
                for i, rls_note_line in enumerate(release_note.text.splitlines()):
                    if i == 0:
                        release_note_lines.append(
                            '* {rls_note_line} (#{pr_num}, @{user})'
                                .format(
                                    pr_num=release_note.pr_number,
                                    user=release_note.user_login,
                                    rls_note_line=rls_note_line
                                )
                        )
                    else:
                        release_note_lines.append('  * {rls_note_line}'.format(
                            rls_note_line=rls_note_line
                        ))
        return release_note_lines

    def release_notes_for_category(
        category: Category,
        release_note_objs: list
    ) -> list:
        rn_lines = list()
        rn_lines_category = list()
        for target_group in target_groups:
            rn_lines_category.extend(
                release_notes_for_target_group(
                    category=category,
                    target_group=target_group,
                    release_note_objs=release_note_objs
                )
            )

        if rn_lines_category:
            rn_lines.append(category.title)
            rn_lines.extend(rn_lines_category)
        return rn_lines

    release_note_lines = list()
    for category in categories:
        release_note_lines.extend(release_notes_for_category(
            category=category,
            release_note_objs=release_note_objs
        ))

    if release_note_lines:
        return '\n'.join(release_note_lines)
    else: # fallback
        return 'no release notes available'

def calculate_range(
    repository_branch: str,
    repo: git.Repo,
    helper: GitHubRepositoryHelper,
) -> str:

    branch_head = repo.rev_parse('refs/remotes/origin/' + repository_branch)
    if not branch_head:
        fail('could not determine branch head of {branch} branch'.format(
            branch=repository_branch
        ))
    range_start = _.head(reachable_release_tags_from_commit(helper, repo, branch_head))

    range_end = None
    try:
        range_end = repo.git.describe(branch_head) # better readable range_end by describing head commit
    except git.exc.GitError:
        range_end = branch_head.hexsha

    commit_range = "{start}..{end}".format(start=range_start, end=range_end)
    return commit_range

def release_tags(
    helper: GitHubRepositoryHelper,
    repo: git.Repo
) -> list:
    release_tags = helper.release_tags()
    # you can remove the directive to disable the undefined-variable error once pylint is updated
    # with fix https://github.com/PyCQA/pylint/commit/db01112f7e4beadf7cd99c5f9237d580309f0494 included
    # pylint: disable=undefined-variable
    tags = _ \
        .chain(repo.tags) \
        .map(lambda tag: {"tag": tag.name, "commit": tag.commit.hexsha}) \
        .filter(lambda item: _.find(release_tags, lambda el: el == item['tag'])) \
        .key_by('commit') \
        .map_values('tag') \
        .value()
    # pylint: enable=undefined-variable
    return tags

def reachable_release_tags_from_commit(
    helper: GitHubRepositoryHelper,
    repo: git.Repo,
    commit: git.objects.Commit
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
        not_visited_parents = _.filter(commit.parents,
            lambda parent_commit: not parent_commit.hexsha in visited
        )
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
    pr_numbers = set()
    for commitMessage in gitLogs:
        if commitMessage.startswith('Merge pull'):
            pr_number = _.head(re.findall(r"#(\d+|$)", commitMessage))
            if pr_number:
                pr_numbers.add(pr_number)

    verbose('Merged pull request numbers in range {range}: {pr_numbers}'.format(
        range=commit_range,
        pr_numbers=pr_numbers
    ))
    return pr_numbers

def fetch_release_notes_from_prs(
    helper: GitHubRepositoryHelper,
    pr_numbers_in_range: set
) -> list:
    # we should consider adding a release-note label to the PRs
    # to reduce the number of search results
    prs_iter = helper.search_issues_in_repo('type:pull is:closed')

    release_notes = list()
    for pr_iter in prs_iter:
        pr_dict = pr_iter.as_dict()

        pr_number = pr_dict['number']
        if not str(pr_number) in pr_numbers_in_range:
            continue

        release_notes_pr = extract_release_notes(
            pr_number=pr_number,
            text=pr_dict['body'],
            user_login=_.get(pr_dict, 'user.login')
        )
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

    code_blocks = re.findall(
        r"``` *(improvement|noteworthy)( (user|operator)?)?.*?\n(.*?)\n```",
        text,
        re.MULTILINE | re.DOTALL
    )
    for code_block in code_blocks:
        code_block = _.map(code_block, lambda obj: _.trim(obj))

        text = code_block[3]
        if not text or 'none' == text.lower():
            continue

        category = code_block[0]
        target_group = code_block[2] or 'user'

        release_notes.append(ReleaseNote(
            category_id=category,
            target_group_id=target_group,
            text=text,
            pr_number=pr_number,
            user_login=user_login
        ))
    return release_notes
