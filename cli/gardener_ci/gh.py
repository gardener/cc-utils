import datetime
import dateutil.parser
import pprint
import urllib.parse

import ccc.github
import ci.util
import github.webhook


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


def ls_org_webhooks(org_url: str):
    gh_api = ccc.github.github_api(repo_url=org_url)

    if not '://' in org_url:
        org_url = f'https://{org_url}'

    parsed_url = urllib.parse.urlparse(org_url)

    org_name = parsed_url.path[1:]
    if '/' in org_name:
        org_name = org_name.split('/')[0]

    webhooks = github.webhook.org_webhooks(github_api=gh_api, org_name=org_name).json()

    pprint.pprint(webhooks)


def retrigger_failed_webhooks(org_url: str, hook_id: str, max_age_days:int=2):
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    tdelta = datetime.timedelta(days=max_age_days)
    oldest = now - tdelta

    gh_api = ccc.github.github_api(repo_url=org_url)
    routes = github.webhook.Routes(gh_api)

    if not '://' in org_url:
        org_url = f'https://{org_url}'

    parsed_url = urllib.parse.urlparse(org_url)
    org_name = parsed_url.path[1:]
    if '/' in org_name:
        org_name = org_name.split('/')[0]

    org_hook = gh_api._get(routes.org_hook(name=org_name, id=hook_id))
    org_hook.raise_for_status()
    org_hook = org_hook.json()

    deliveries_url = org_hook['deliveries_url'] + '?per_page=100'

    def iter_deliveries():
        nonlocal deliveries_url

        while True:
            deliveries = gh_api._get(deliveries_url)
            deliveries.raise_for_status()

            for delivery in deliveries.json():
                delivered_at = dateutil.parser.isoparse(delivery['delivered_at'])
                if delivered_at < oldest:
                    print(f'reached sufficiently old delivery: {delivered_at=}')
                    return

                status = delivery['status']
                if status == 'OK':
                    continue

                yield delivery

            # next chunk; hackily parse from <url>; rel="next"
            deliveries_url = deliveries.headers['Link'][1:].split('>')[0]

    for failed_delivery in iter_deliveries():
        delivery_id = str(failed_delivery['id'])
        redelivery_url = routes.org_hook_delivery_atttemps(
            name=org_name,
            hook_id=hook_id,
            delivery_id=delivery_id,
        )

        print(f'retriggering hook: {redelivery_url=}')
        res = gh_api._post(redelivery_url)
        res.raise_for_status()
