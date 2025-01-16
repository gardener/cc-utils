#!/usr/bin/env python

import argparse
import os
import pprint
import sys
import tempfile

import github3
import yaml

try:
    import ocm
except ImportError:
    # make local development more comfortable
    repo_root = os.path.join(os.path.dirname(__file__), '../../..')
    sys.path.insert(1, repo_root)
    print(f'note: added {repo_root} to python-path (sys.path)')
    import ocm

import cnudie.retrieve
import github.util
import gitutil
import oci.auth
import oci.client
import release_notes.fetch
import release_notes.markdown
import version


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--component-descriptor',
        default='component-descriptor.yaml',
        help='path to component-descriptor file to read from',
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
        '--repo-worktree',
        default=os.getcwd(),
        help='path to root-component\'s worktree root',
    )
    parser.add_argument(
        '--draftname-suffix',
        default='-draft',
    )

    parsed = parser.parse_args()
    pprint.pprint(parsed)

    with open(parsed.component_descriptor) as f:
        component_descriptor = ocm.ComponentDescriptor.from_dict(
            yaml.safe_load(f)
        )

    component = component_descriptor.component
    # effective component-descriptor will be default contain either "-next"-version, or
    # effective version (which is suffixed w/ commit-digests). hardcode conversion to
    # next final version (might make this configurable later, if needed)
    component.version = version.process_version(
        component.version,
        operation='finalise',
    )

    oci_client = oci.client.Client(
        credentials_lookup=oci.auth.docker_credentials_lookup(),
    )
    ocm_repository_lookup = cnudie.retrieve.ocm_repository_lookup(component.current_ocm_repo)

    component_descriptor_lookup = cnudie.retrieve.create_default_component_descriptor_lookup(
        ocm_repository_lookup=ocm_repository_lookup,
        oci_client=oci_client,
        cache_dir=tempfile.TemporaryDirectory().name,
    )
    ocm_version_lookup = cnudie.retrieve.version_lookup(
        ocm_repository_lookup=ocm_repository_lookup,
        oci_client=oci_client,
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

    github_helper = github.util.GitHubRepositoryHelper(
        owner=org,
        name=repo,
        github_api=github_api,
    )
    git_helper = gitutil.GitHelper(
        repo=parsed.repo_worktree,
        git_cfg=gitutil.GitCfg(
            repo_url=f'https://{host}/{org}/{repo}',
            user_name='Gardener-CICD-GitHubAction-Bot',
            user_email='no-reply@github.com',
            auth=None,
            auth_type=gitutil.AuthType.PRESET,
        ),
    )

    def github_api_lookup(repo_url):
        # XXX: needs to be extended for cross-github-support
        return github_api

    try:
        release_notes_md = 'no release notes available'
        release_note_blocks = release_notes.fetch.fetch_draft_release_notes(
            current_version=component.version,
            component=component,
            component_descriptor_lookup=component_descriptor_lookup,
            version_lookup=ocm_version_lookup,
            git_helper=git_helper,
            github_api_lookup=github_api_lookup,
        )
    except ValueError as ve:
        print(f'Warning: error whilst fetch draft-release-notes: {ve=}')
        import traceback
        traceback.print_exc()
        release_note_blocks = None

    if release_note_blocks:
        release_notes_md = '\n'.join(
            str(rn) for rn
            in release_notes.markdown.render(release_note_blocks)
        )

    draft_release_name = f'{component.version}{parsed.draftname_suffix}'
    if not (draft_release := github_helper.draft_release_with_name(draft_release_name)):
        print(f'Creating {draft_release_name=}')
        github_helper.create_draft_release(
            name=draft_release_name,
            body=release_notes_md,
        )
    else:
        if not draft_release.body == release_notes_md:
            print(f'Updating {draft_release_name=}')
            draft_release.edit(body=release_notes_md)

    for release, deleted in github_helper.delete_outdated_draft_releases():
        if deleted:
            print('Deleted obsolete draft {release.name=}')
        else:
            print(f'Failed to delete draft {release.name=}')


if __name__ == '__main__':
    main()
