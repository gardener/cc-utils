import checkmarx.facade
import checkmarx.client


def upload_and_scan_repo(
    checkmarx_cfg_name: str,
    team_id: str,
    github_repo_url: str,
    ref: str = 'refs/heads/master'
):
    project_facade = checkmarx.facade.create_project_facade(
        checkmarx_cfg_name=checkmarx_cfg_name,
        team_id=team_id,
        component_name=github_repo_url
    )

    project_facade.upload_source(ref=ref)
    # project_facade.start_scan_and_poll()
