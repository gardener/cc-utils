import dependabot.status


def coverage_report(
    github_hostname: str,
    org: str,
    outfile_path: str,
):

    dependabot.status.status_for_org(
        github_hostname=github_hostname,
        org=org,
        outfile_path=outfile_path,
    )
