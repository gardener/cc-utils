import collections.abc
import traceback

import github3


def is_user_trusted(
    login: str,
    github_api: github3.GitHub,
    trusted_org: str,
    trusted_teams: collections.abc.Iterable[str],
    missing_privileges_ok: bool=False,
    debug: bool=False,
):
    try:
        org = github_api.organization(trusted_org)
    except github3.exceptions.ForbiddenError:
        if missing_privileges_ok:
            print(
                'Warning: cannot read organisation - probably passed-in token lacks permissions'
            )
            if debug:
                traceback.print_exc()
            return False
        else:
            raise

    if org.is_member(login):
        return True

    try:
        for team in org.teams():
            if not team.name in trusted_teams:
                continue
            for member in team.members():
                if member.login == login:
                    return True
    except github3.exceptions.ForbiddenError:
        print(
            'Warning: cannot read teams - probably passed-in token lacks permissions'
        )
        if debug:
            traceback.print_exc()
        return False

    return False
