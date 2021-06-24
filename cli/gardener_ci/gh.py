import ccc.github
import ci.util


def ls_team_members(host: str, org: str, teams: [str]):
    gh_cfg = ccc.github.github_cfg_for_repo_url(repo_url=ci.util.urljoin(host, org))
    api = ccc.github.github_api(gh_cfg)

    gh_org = api.organization(org)
    teams = [
        t for t in gh_org.teams()
        if t.name in teams
    ]

    teams_to_user_ids = {}
    for t in teams:
        member_logins = [m.login for m in t.members()]
        teams_to_user_ids[t.name] = member_logins

    # use copy-paste-friendly format
    for t, ids in teams_to_user_ids.items():
        print(f'{t}: {", ".join(ids)}')
