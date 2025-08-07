#!/usr/bin/env python

# note: do not name this file `release_notes.py` to avoid conflicts w/ package of this name

import argparse
import logging
import os
import pprint
import sys
import tempfile

import yaml

try:
    import ocm
except ImportError:
    # make local development more comfortable
    repo_root = os.path.join(os.path.dirname(__file__), '../../..')
    sys.path.insert(1, repo_root)
    print(f'note: added {repo_root} to python-path (sys.path)', file=sys.stderr)
    import ocm

import cnudie.retrieve
import github
import gitutil
import oci.auth
import oci.client
import ocm.gardener
import release_notes.fetch
import release_notes.markdown
import release_notes.ocm as rno
import version

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
)
logging.getLogger('github3').setLevel(logging.DEBUG) # silence verbose logger from github3


def ensure_trailing_newline(text: str) -> str:
    if not text or text.endswith('\n'):
        return text
    return f'{text}\n'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--component-descriptor',
        default='component-descriptor.yaml',
        help='path to component-descriptor file to read from',
    )
    parser.add_argument(
        '--ocm-repositories',
        action='extend',
        type=lambda repo: repo.split(','),
        default=[],
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
        '--draft',
        action='store_true',
        default=False,
        help='if set, will fetch draft-release-notes',
    )
    parser.add_argument(
        '--full-release-notes',
        default='-',
        help='''\
            output file to write to (`-` for stdout, which is the default).
            full release-notes contain release-notes from sub-components.
        ''',
    )
    parser.add_argument(
        '--local-release-notes',
        default=None,
    )
    parser.add_argument(
        '--subcomponent-release-notes',
        default=None,
        help='release-notes from sub-components',
    )
    parser.add_argument(
        '--no-subcomponent-release-notes',
        action='store_true',
        default=False,
        help='if passed, no subcomponent-release-notes will be fetched',
    )

    parsed = parser.parse_args()
    if parsed.no_subcomponent_release_notes:
        # patch passed outfile for convenience (so caller may always specify it, and does not need
        # to calculate ARGV dynamically
        parsed.subcomponent_release_notes = False

    print(pprint.pformat(parsed), file=sys.stderr)

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
    ocm_repository_lookup = cnudie.retrieve.ocm_repository_lookup(
        *parsed.ocm_repositories,
        component.current_ocm_repo,
    )

    component_descriptor_lookup = cnudie.retrieve.create_default_component_descriptor_lookup(
        ocm_repository_lookup=ocm_repository_lookup,
        oci_client=oci_client,
        cache_dir=tempfile.TemporaryDirectory().name,
    )
    ocm_version_lookup = cnudie.retrieve.version_lookup(
        ocm_repository_lookup=ocm_repository_lookup,
        oci_client=oci_client,
    )

    host, org, repo = github.host_org_and_repo(
        repo_url=parsed.repo_url,
    )
    github_api = github.github_api(
        repo_url=parsed.repo_url,
        token=parsed.github_auth_token,
    )

    git_helper = gitutil.GitHelper(
        repo=parsed.repo_worktree,
        git_cfg=gitutil.GitCfg(repo_url=f'https://{host}/{org}/{repo}'),
    )

    # pass current head as ref-commit. This avoids release-tag to exist in remote while
    # fetching release-notes
    repository = git_helper.repo
    version_whither_ref_commit = repository.head.commit

    def github_api_lookup(repo_url):
        # XXX: needs to be extended for cross-github-support
        return github_api

    try:
        release_notes_md = ''
        if parsed.draft:
            release_note_blocks = release_notes.fetch.fetch_draft_release_notes(
                component=component,
                component_descriptor_lookup=component_descriptor_lookup,
                version_lookup=ocm_version_lookup,
                git_helper=git_helper,
                github_api_lookup=github_api_lookup,
                version_whither=component.version,
            )
        else:
            release_note_blocks = release_notes.fetch.fetch_release_notes(
                component=component,
                component_descriptor_lookup=component_descriptor_lookup,
                version_lookup=ocm_version_lookup,
                git_helper=git_helper,
                github_api_lookup=github_api_lookup,
                version_whither=component.version,
                version_whither_ref_commit=version_whither_ref_commit,
            )
    except Exception as ve:
        print(f'Warning: error whilst fetch draft-release-notes: {ve=}')
        import traceback
        traceback.print_exc(file=sys.stderr)
        release_note_blocks = None

    if release_note_blocks:
        release_notes_md = ensure_trailing_newline('\n'.join(
            str(rn) for rn
            in release_notes.markdown.render(release_note_blocks)
        ))

    version_vector = ocm.gardener.UpgradeVector(
        whence=ocm.ComponentIdentity(
            name=component.name,
            version=version.find_predecessor(
                version=component.version,
                versions=[v for v in ocm_version_lookup(component) if version.is_final(v)],
            ),
        ),
        whither=ocm.ComponentIdentity(
            name=component.name,
            version=component.version,
        ),
    )

    # retrieve release-notes from sub-components
    whence_component = component_descriptor_lookup(version_vector.whence).component

    if parsed.subcomponent_release_notes:
        sub_component_release_notes_docs = list(rno.release_notes_for_subcomponents(
            whence_component=whence_component,
            whither_component=component,
            component_descriptor_lookup=component_descriptor_lookup,
            version_lookup=ocm_version_lookup,
            oci_client=oci_client,
            version_filter=version.is_final,
        ))

        grouped_sub_component_release_notes_docs = rno.group_release_notes_docs(
            release_notes_docs=sub_component_release_notes_docs,
        )

        sub_component_release_notes = ensure_trailing_newline(
            rno.release_notes_docs_as_markdown(
                release_notes_docs=grouped_sub_component_release_notes_docs,
                prepend_title=False,
            ),
        )
    else:
        sub_component_release_notes = None

    if sub_component_release_notes:
        full_release_notes_md = f'{release_notes_md}\n{sub_component_release_notes}'
    else:
        full_release_notes_md = release_notes_md

    if parsed.full_release_notes == '-':
        sys.stdout.write(full_release_notes_md)
    else:
        with open(parsed.full_release_notes, 'w') as f:
            f.write(full_release_notes_md)

    if parsed.local_release_notes:
        with open(parsed.local_release_notes, 'w') as f:
            f.write(release_notes_md)

    if parsed.subcomponent_release_notes:
        with open(parsed.subcomponent_release_notes, 'w') as f:
            f.write(sub_component_release_notes)


if __name__ == '__main__':
    main()
