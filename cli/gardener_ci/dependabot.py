import dso.dependabot


def coverage_report(
    org: str,
    outfile_path: str,
):

    dso.dependabot.dependabot_coverage(
        org=org,
        outfile_path=outfile_path,
    )
