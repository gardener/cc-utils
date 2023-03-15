import json
from typing import Optional

import git
from github3 import GitHub, pulls, exceptions
from github3.structs import GitHubIterator


# pylint: disable=protected-access
# noinspection PyProtectedMember
def list_associated_pulls(gh: GitHub, owner: str, repo: str, sha: str) -> Optional[tuple[pulls.ShortPullRequest]]:
    ''' Returns a tuple with pull requests related to the specified commit.

    :param gh: Instance of the GitHub v3 API
    :param owner: Owner of the repository (on GitHub)
    :param repo: Name of the repository (on GitHub)
    :param sha: SHA of the commit
    :return: a tuple with pull requests related to the specific commit
    '''
    try:
        url = gh._build_url('repos', owner, repo, 'commits', sha, 'pulls')
        return tuple(gh._iter(-1, url, pulls.ShortPullRequest))
    except exceptions.UnprocessableEntity as e:
        if e.code != 422:  # pull request not found
            raise e
        return None


# pylint: disable=protected-access
# noinspection PyProtectedMember
def list_pulls(gh: GitHub, owner: str, repo: str, state: str = 'closed') -> GitHubIterator[pulls.ShortPullRequest]:
    url = gh._build_url('repos', owner, repo, 'pulls') + '?state=' + state
    return gh._iter(-1, url, pulls.ShortPullRequest)


def add_payload_to_git_notes(repo: git.Repo, commit: git.Commit, payload: dict):
    ''' Notes can be attached to a commit using
    `$ git notes add -m <message> <sha>`

    :param repo: the repository the commit belongs to
    :param commit: the commit to add notes to
    :param payload: the payload to write to the commit notes
    :return:
    '''
    repo.git.notes('add', '-f', '-m', json.dumps(payload), commit.hexsha)


def shorten(message: str, max_len: int = 128) -> str:
    message = message.replace('\n', '\\n')
    if len(message) > max_len:
        message = message[:max_len - 3] + '...'
    return message
