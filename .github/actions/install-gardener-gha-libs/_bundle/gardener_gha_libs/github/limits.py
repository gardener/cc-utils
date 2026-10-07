'''
limits for github-api

stolen from: https://github.com/dead-claudia/github-limits

limits refer to amount of codepoints (tested empirically for some samples).
'''

issue_body = 65536
comment_body = 65536
issue_title = 256
pullrequest_body = 262144
release_body = 125000
label = 50


def fits(
    value: str | bytes,
    /,
    limit: int,
) -> bool:
    return len(value) <= limit
