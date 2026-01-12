import argparse
import functools
import tempfile
import urllib.parse

import git
import github3
import github3.repos.repo

import cnudie.retrieve
import oci.client
import ocm
import ocm.gardener

import ocm_upgrade as ou


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--component-name',
        required=True,
        help='OCM component to upgrade',
    )
    parser.add_argument(
        '--from',
        required=True,
        dest='version_from',
        help='starting OCM version of upgrade vector',
    )
    parser.add_argument(
        '--to',
        required=True,
        dest='version_to',
        help='target OCM version of upgrade vector',
    )
    parser.add_argument(
        '--ocm-repo',
        required=False,
        dest='ocm_repos',
        default=['europe-docker.pkg.dev/gardener-project/releases'],
        action='append',
        help='locations to lookup OCM component descriptors',
    )
    parser.add_argument(
        '--gh-token',
        required=True,
        help='github access token used to create Upgrade PR',
    )
    parser.add_argument(
        '--repo-url',
        required=True,
        help='github repository to create Upgrade PR in, pattern: "<gh-host>/<org>/<owner>"',
    )
    parser.add_argument(
        '--automerge',
        required=False,
        default=False,
        help='whether to also merge PR, requires gh-token to be priviledged accordingly',
        type=bool,
    )
    parser.add_argument(
        '--merge-method',
        required=False,
        default='merge',
        help='choose how to merge the PR, only used if "automerge" is set. possible values: \
            "merge", "rebase", "squash"'
    )
    parser.add_argument(
        '--docker-cfg',
        required=False,
        default='~/.docker/config.json',
        help='path to credential file used for OCI interaction',
    )
    parser.add_argument(
        '--base-branch',
        default='master',
        help='target branch to create pull-request against',
    )
    parsed = parser.parse_args()

    upgrade_vector = ocm.gardener.UpgradeVector(
        whence=ocm.ComponentIdentity(
            name=parsed.component_name,
            version=parsed.version_from,
        ),
        whither=ocm.ComponentIdentity(
            name=parsed.component_name,
            version=parsed.version_to,
        ),
    )

    parsed_url = urllib.parse.urlparse(parsed.repo_url)
    host, org, repo = parsed_url.path.split('/')

    if host == 'github.com':
        gh_api_ctor = github3.github.GitHub
    else:
        gh_api_ctor = functools.partial(
            github3.github.GitHubEnterprise,
            verify=True,
            url=parsed.repo_url,

        )

    gh_api = gh_api_ctor(token=parsed.gh_token)
    repository = gh_api.repository(owner=org, repository=repo)

    oci_client = oci.client.client_with_dockerauth()

    ocm_repository_lookup = cnudie.retrieve.ocm_repository_lookup(
        *parsed.ocm_repos,
    )
    version_lookup = cnudie.retrieve.version_lookup(
        ocm_repository_lookup=ocm_repository_lookup,
        oci_client=oci_client,
    )

    print(
        f'trying to create OCM Upgrade PR for {parsed.component_name} {parsed.version_from} ->'
        f' {parsed.version_to} in {parsed.repo_url}'
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        repository: github3.repos.repo.Repository

        git.Repo.clone_from(
            url=repository.clone_url.replace(
                'https://',
                f'https://{parsed.gh_token}@'
            ),
            to_path=tmpdir,
        )

        pr = ou.create_upgrade_pullrequest(
            upgrade_vector=upgrade_vector,
            component_descriptor_lookup=cnudie.retrieve.create_default_component_descriptor_lookup(
                oci_client=oci_client,
            ),
            version_lookup=version_lookup,
            repo_dir=tmpdir,
            repo_url=parsed.repo_url,
            repository=repository,
            merge_policy=ou.MergePolicy.AUTOMERGE if parsed.automerge else ou.MergePolicy.MANUAL,
            merge_method=ou.MergeMethod(parsed.merge_method),
            branch='main',
            oci_client=oci_client,
        )
        print(f'Upgrade pull-request was created: {pr.pull_request.html_url}')
