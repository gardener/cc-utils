#!/usr/bin/env python

import argparse
import os
import pprint
import sys

import github3

try:
    import github.release
except ImportError:
    # make local development more comfortable
    repo_root = os.path.join(os.path.dirname(__file__), '../../..')
    sys.path.insert(1, repo_root)
    print(f'note: added {repo_root} to python-path (sys.path)')
    import github.release

import version as version_mod


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--release-notes',
        default='release-notes.md',
        help='path to file to read release-notes from',
    )
    parser.add_argument(
        '--repo-url',
        required=False,
        default=None,
        help='github-repo-url ({host}/{org}/{repo}). derived from GitHubActions-Env-Vars by default',
    )
    # TODO: needs to be extended in order to support authentication against multiple GH(E)-instances
    # -> as cross-auth is not in scope for first version of this script, omitted this on purpose
    parser.add_argument(
        '--github-auth-token',
        default=os.environ.get('GITHUB_TOKEN', None),
        help='the github-auth-token to use (defaults to GitHub-Action\'s default',
    )
    parser.add_argument(
        '--version',
        help='the draft-release\'s version (will be finalised if not a final version)',
    )
    parser.add_argument(
        '--draftname-suffix',
        default='-draft',
    )

    parsed = parser.parse_args()
    pprint.pprint(parsed)

    # effective or raw version will often be non-final; finalise for convenience
    version = version_mod.process_version(
        parsed.version,
        operation='finalise',
    )

    if (repo_url := parsed.repo_url):
        host, org, repo = repo_url.strip('/').split('/')
    else:
        host = os.environ['GITHUB_SERVER_URL'].removeprefix('https://')
        org, repo = os.environ['GITHUB_REPOSITORY'].split('/')

    if host == 'github.com':
        github_api = github3.GitHub(token=parsed.github_auth_token)
    else:
        github_api = github3.GitHubEnterprise(
            url=f'https://{host}', # yes, slightly hacky (but good enough for now)
            token=parsed.github_auth_token,
        )

    repository = github_api.repository(org, repo)

    with open(parsed.release_notes) as f:
        release_notes_md = f.read()

    draft_release_name = f'{version}{parsed.draftname_suffix}'
    release_notes_md, _ = github.release.body_or_replacement(
        release_notes_md,
    )
    if not (draft_release := github.release.find_draft_release(
        repository=repository,
        name=draft_release_name,
    )):
        print(f'Creating {draft_release_name=}')
        repository.create_release(
            tag_name=draft_release_name,
            body=release_notes_md,
            draft=True,
        )
    else:
        if not draft_release.body == release_notes_md:
            print(f'Updating {draft_release_name=}')
            draft_release.edit(body=release_notes_md)

    for release, deleted in github.release.delete_outdated_draft_releases(repository):
        if deleted:
            print('Deleted obsolete draft {release.name=}')
        else:
            print(f'Failed to delete draft {release.name=}')


if __name__ == '__main__':
    main()
