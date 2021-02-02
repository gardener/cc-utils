import dependabot.status


def coverage_report(
    full_org_name: str,
    outfile_path: str,
):

    dependabot.status.status_for_org(
        full_org_name=full_org_name,
        outfile_path=outfile_path,
    )
