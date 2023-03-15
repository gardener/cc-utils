import collections
import json
import logging
import time
from typing import Optional

import git
import git.exc
import github3.pulls
import github3.repos

import release_notes.utils as rnu

_git_notes_section_pulls_key = 'associated-pulls'
_git_notes_created_key = 'checked-at'
_git_notes_pulls_key = 'associated-pull-numbers'


def _find_payload_from_git_notes(repo: git.Repo, commit: git.Commit) -> Optional[dict]:
    ''' Notes can be read from a commit using
    `$ git notes show <sha>`

    :param repo: the repository the commit belongs to
    :param commit: the commit to read the payload from
    :return: the note contents parsed as JSON
    '''
    try:
        note: str = repo.git.notes('show', commit.hexsha)
        res = json.loads(note)
        if not isinstance(res, dict):
            raise RuntimeError('cannot convert payload from JSON to dict', note)
        return res
    except git.exc.GitCommandError:
        return None
    except json.JSONDecodeError:
        return None  # if the note doesn't contain valid JSON, we don't care about _that_ note


def _find_pull_numbers_from_git_notes(repo: git.Repo, commit: git.Commit) -> Optional[tuple[int]]:
    if not (payload := _find_payload_from_git_notes(repo, commit)):
        return None
    if not (section := payload.get(_git_notes_section_pulls_key)):
        return None
    return section.get(_git_notes_pulls_key)


def _add_pull_numbers_to_git_notes(repo: git.Repo, commit: git.Commit, prs: list[int]):
    rnu.add_payload_to_git_notes(repo, commit, {
        _git_notes_section_pulls_key: {
            _git_notes_created_key: int(time.time()),
            _git_notes_pulls_key: prs,
        }
    })


def request_pulls_from_api(repo: git.Repo,
                           gh: github3.GitHub,
                           owner: str,
                           repo_name: str,
                           commits: list[git.Commit]) -> dict[str, list[github3.pulls.ShortPullRequest]]:
    ''' We use notes to store the associated pull request numbers to reduce requests to GitHub (rate limiting).
    The corresponding pull request number is stored in a note.
    We can then fetch a list of pull requests for a repository and thus (theoretically) process
    100 pull requests with one API call in the best case.

    If there is no note, request the "normal" API route to retrieve associated pull requests and
    store the pull-numbers in the commit note.
    '''
    # pr_number -> [ list of commit sha ]
    pending = collections.defaultdict(list)
    # commit_sha -> [ list of pull requests ]
    result = collections.defaultdict(list)

    for commit in commits:
        if nums := _find_pull_numbers_from_git_notes(repo, commit):
            for num in nums:
                pending[num].append(commit.hexsha)
            continue

        if prs := rnu.list_associated_pulls(gh, owner, repo_name, commit.hexsha):
            # add all found pull requests to the result right away
            result[commit.hexsha].extend(prs)
            _add_pull_numbers_to_git_notes(repo, commit, [z.number for z in prs])

    if len(pending) > 0:
        for pull in rnu.list_pulls(gh, owner, repo_name):
            if pull.number in pending:
                for sha in pending[pull.number]:
                    result[sha].append(pull)
                del pending[pull.number]
            if len(pending) == 0:
                break
        else:
            logging.warning('couldn\'t find all pending pull requests')

    return result
