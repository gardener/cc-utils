#!/usr/bin/env python

# note: do not name this file `release_notes.py` to avoid conflicts w/ package of this name

import argparse
import logging
import os
import pprint
import sys

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
import release_notes.fetch as rnf
import release_notes.ocm as rno
import release_notes.tarutil as rnt
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
    parser.add_argument(
        '--tar-output',
        default=None,
        help='Path to write machine-readable release-notes archive (.tar)',
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
        component_release_notes_doc, subcomponent_release_notes_docs = rnf.collect_release_notes(
            git_helper=git_helper,
            release_version=component.version,
            component=component,
            version_lookup=ocm_version_lookup,
            github_api_lookup=github_api_lookup,
            version_whither_ref_commit=version_whither_ref_commit,
            is_draft=parsed.draft,
        )
    except Exception as e:
        print(f'Warning: error whilst fetch release-notes: {e=}')
        import traceback
        traceback.print_exc(file=sys.stderr)
        component_release_notes_doc = None
        subcomponent_release_notes_docs = []

    if component_release_notes_doc:
        release_notes_md = ensure_trailing_newline(rno.release_notes_docs_as_markdown(
            release_notes_docs=[component_release_notes_doc],
        ))
    else:
        release_notes_md = ''

    if parsed.subcomponent_release_notes:
        sub_component_release_notes = ensure_trailing_newline(rno.release_notes_docs_as_markdown(
            release_notes_docs=subcomponent_release_notes_docs,
        ))
    else:
        sub_component_release_notes = None

    if release_notes_md and sub_component_release_notes:
        full_release_notes_md = f'{release_notes_md}\n{sub_component_release_notes}'
    elif sub_component_release_notes:
        full_release_notes_md = sub_component_release_notes
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

    if parsed.tar_output:
        all_release_note_docs = subcomponent_release_notes_docs
        if component_release_notes_doc:
            all_release_note_docs.append(component_release_notes_doc)

        with open(parsed.tar_output, 'wb') as f:
            for chunk in rnt.release_notes_docs_into_tarstream(all_release_note_docs):
                f.write(chunk)


if __name__ == '__main__':
    main()
