'''
utils wrapping github3.py's relase-API
'''

import github3.repos
import github3.repos.release

import github.limits


def body_or_replacement(
    body: str,
    replacement: str='body was too large (limit: {limit} / actual: {actual})',
    limit: int=github.limits.release_body,
) -> tuple[str, bool]:
    '''
    convenience function that will check whether given body is short enough to be accepted
    by GitHub's API. If so, passed body will be returned as first element of returned tuple, else
    replacement value.

    The second value of returned tuple will indicate whether original body was returned. Callers
    may use this hint to perform a mitigation.

    limit may be overwritten (but this is not recommended; see github.limits for more details).
    '''
    if github.limits.fits(
        body,
        limit=limit,
    ):
        return body, True

    return replacement.format(
        limit=limit,
        actual=len(body),
    ), False


def find_draft_release(
    repository: github3.repos.Repository,
    name: str,
) -> github3.repos.release.Release | None:
    '''
    finds the given draft-release. For draft-releases, lookup has to be done that way, as
    there is no way of directly retrieving a draft-release (as those do not yet have a tag)
    '''
    # at some point in time, github.com would return http-500 if there were more than 1020
    # releases; as draft-releases are typically not too old (and such great numbers of releases
    # are uncommon), this should be okay to hardcode. Todo: check whether this limit is still
    # valid.
    max_releases = 1020
    for release in repository.releases(number=max_releases):
        if not release.draft:
            continue
        if release.name == name:
            return release
