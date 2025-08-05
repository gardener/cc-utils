#!/usr/bin/env python

import collections.abc
import logging
import os
import subprocess
import sys

try:
    import ocm
except ImportError:
    # make local development more convenient
    repo_root = os.path.join(os.path.dirname(__file__), '../../..')
    sys.path.insert(1, repo_root)
    import ocm

import github3.repos
import yaml

import cnudie.retrieve
import github.pullrequest
import gitutil
import oci.auth
import oci.client
import ocm.base_component
import ocm.gardener
import release_notes.ocm as rno
import version

logger = logging.getLogger(__name__)


def create_ocm_lookups(
    ocm_repositories: collections.abc.Iterable[str],
) -> tuple[
    oci.client.Client,
    ocm.ComponentDescriptorLookup,
    ocm.VersionLookup,
]:
    oci_client = oci.client.Client(
        credentials_lookup=oci.auth.docker_credentials_lookup(),
    )
    ocm_repository_lookup = cnudie.retrieve.ocm_repository_lookup(
        *ocm_repositories,
    )

    component_descriptor_lookup = cnudie.retrieve.create_default_component_descriptor_lookup(
        ocm_repository_lookup=ocm_repository_lookup,
        oci_client=oci_client,
        cache_dir=None,
    )

    version_lookup = cnudie.retrieve.version_lookup(
        ocm_repository_lookup=ocm_repository_lookup,
        oci_client=oci_client,
    )

    return oci_client, component_descriptor_lookup, version_lookup


def create_diff_in_base_component(
    upgrade_vector: ocm.gardener.UpgradeVector,
    repo_dir: str,
    rel_path: str='.ocm/base-component.yaml',
) -> bool:
    path = os.path.join(repo_dir, rel_path)
    if not os.path.isfile(path):
        return False

    base_component = ocm.base_component.load_base_component(
        path=path,
        absent_ok=False,
    )

    for cref in base_component.componentReferences:
        if cref.componentName == upgrade_vector.component_name:
            break
    else:
        return False # did not find matching cref

    # need to take low-level approach, as we need to avoid adding default attributes from
    # BaseComponent (or dropping extra attributes)
    with open(path) as f:
        base_component = yaml.safe_load(f)

    for cref in base_component['componentReferences']:
        cname = cref['componentName']
        cver = cref['version']

        if cname != upgrade_vector.component_name:
            continue

        # sanity-check: whence-version must match
        if cver != upgrade_vector.whence.version:
            logger.warning(f'{cname}:{cver} does not match {upgrade_vector.whence=} - skipping')
            continue

        break
    else:
        return False

    # we found a reasonable candidate
    cref['version'] = upgrade_vector.whither.version

    with open(path, 'w') as f:
        yaml.safe_dump(base_component, f)

    return True


def create_diff_using_callback(
    upgrade_vector: ocm.gardener.UpgradeVector,
    repo_dir: str,
    rel_path: str,
) -> bool:
    path = os.path.join(repo_dir, rel_path)
    if not os.path.isfile(path):
        return False

    cmd_env = github.pullrequest.set_dependency_cmd_env(
        upgrade_vector=upgrade_vector,
        repo_dir=repo_dir,
    )

    subprocess.run(
        (path,),
        check=True,
        env=cmd_env,
    )

    return True


def create_upgrade_pullrequest_diff(
    upgrade_vector: ocm.gardener.UpgradeVector,
    repo_dir: str,
) -> bool:
    created_diff = False

    if create_diff_in_base_component(
        upgrade_vector=upgrade_vector,
        repo_dir=repo_dir,
        rel_path='.ocm/base-component.yaml',
    ):
        logger.info('created upgrade-diff in base-component')
        created_diff = True

    if create_diff_using_callback(
        upgrade_vector=upgrade_vector,
        repo_dir=repo_dir,
        rel_path='.ci/set_dependency_version',
    ):
        logger.info('created upgrade-diff using callback')
        created_diff = True

    return created_diff


def retrieve_release_notes(
    upgrade_vector,
    version_lookup,
    version_filter,
    oci_client: oci.client.Client,
    component_descriptor_lookup,
) -> str | None:
    logger.info(f'fetching release-notes for {upgrade_vector=}')

    release_notes = '\n'.join((
        rno.release_notes_markdown_with_heading(cid, rn)
        for cid, rn in rno.release_notes_range_recursive(
            version_vector=upgrade_vector,
            component_descriptor_lookup=component_descriptor_lookup,
            version_lookup=version_lookup,
            version_filter=version_filter,
            oci_client=oci_client,
        )
    ))

    if release_notes:
        logger.info(f'fetched {len(release_notes)=} characters of release-notes')
    else:
        logger.warning('did not find any release-notes')

    if not release_notes:
        return None

    return f'**Release Notes**:\n{release_notes}'


def create_upgrade_pullrequest(
    upgrade_vector,
    component: ocm.Component,
    component_descriptor_lookup,
    version_lookup,
    github_api_lookup,
    repo_dir: str,
    repo_url: str,
    repository: github3.repos.Repository,
    auto_merge: bool,
    merge_method: str,
    branch: str,
    oci_client: oci.client.Client,
) -> github.pullrequest.UpgradePullRequest:
    logger.info(f'found {upgrade_vector=}')
    git_helper = gitutil.GitHelper(
        repo=repo_dir,
        git_cfg=gitutil.GitCfg(repo_url=repo_url),
    )
    from_component_descriptor = component_descriptor_lookup(
        upgrade_vector.whence,
        absent_ok=False,
    )
    from_component = from_component_descriptor.component

    to_component_descriptor = component_descriptor_lookup(
        upgrade_vector.whither,
        absent_ok=False,
    )
    to_component = to_component_descriptor.component

    release_notes = retrieve_release_notes(
        upgrade_vector=upgrade_vector,
        version_lookup=version_lookup,
        version_filter=version.is_final,
        oci_client=oci_client,
        component_descriptor_lookup=component_descriptor_lookup,
    )

    bom_diff_markdown = github.pullrequest.bom_diff(
        delivery_dashboard_url=None, # XXX add URL once delivery-dashboard is available publicly
        from_component=from_component,
        to_component=to_component,
        component_descriptor_lookup=component_descriptor_lookup,
    )

    pullrequest_body, extra_bodyparts = github.pullrequest.upgrade_pullrequest_body(
        release_notes=release_notes,
        bom_diff_markdown=bom_diff_markdown,
    )

    create_upgrade_pullrequest_diff(
        upgrade_vector=upgrade_vector,
        repo_dir=repo_dir,
    )

    fv = upgrade_vector.whence.version
    tv = upgrade_vector.whither.version
    commit_message = f'Upgrade {upgrade_vector.component_name}\n\nfrom {fv} to {tv}'

    with github.pullrequest.commit_and_push_to_tmp_branch(
        repository=repository,
        git_helper=git_helper,
        commit_message=commit_message,
        target_branch=branch,
        delete_on_exit=auto_merge,
    ) as upgrade_branch_name:
        pull_request = repository.create_pull(
            title=github.pullrequest.upgrade_pullrequest_title(
                upgrade_vector=upgrade_vector,
            ),
            base=branch,
            head=upgrade_branch_name,
            body=pullrequest_body,
        )

        for extra_bodypart in extra_bodyparts:
            pull_request.create_comment(body=extra_bodypart)

        if auto_merge:
            logger.info(f'Merging PR#{pull_request.number} -> {branch=}')
            pull_request.merge(
                merge_method=merge_method,
            )

    return github.pullrequest.as_upgrade_pullrequest(pull_request)


def upgrade_pullrequest_exists(
    upgrade_vector: ocm.gardener.UpgradeVector,
    upgrade_pullrequests: collections.abc.Collection[github.pullrequest.UpgradePullRequest],
) -> bool:
    for upgrade_pullrequest in upgrade_pullrequests:
        if upgrade_pullrequest.upgrade_vector == upgrade_vector:
            return True
    return False


def create_upgrade_pullrequests(
    component: ocm.Component,
    component_descriptor_lookup,
    version_lookup,
    github_api_lookup,
    upgrade_pullrequests: collections.abc.Collection[github.pullrequest.UpgradePullRequest],
    repo_dir: str,
    repo_url: str,
    repository: github3.repos.Repository,
    auto_merge: bool,
    merge_method: str,
    branch: str,
    oci_client: oci.client.Client,
) -> collections.abc.Iterable[github.pullrequest.UpgradePullRequest]:
    for cref in ocm.gardener.iter_component_references(
        component=component,
    ):
        logger.info(f'processing {cref=}')

        upgrade_vector = ocm.gardener.find_upgrade_vector(
            component_id=cref.component_id,
            version_lookup=version_lookup,
            ignore_prerelease_versions=True,
            ignore_invalid_semver_versions=True,
        )

        if not upgrade_vector:
            logger.info(f'did not find an upgrade-proposal for {cref=}')
            continue

        if upgrade_pullrequest_exists(
            upgrade_vector=upgrade_vector,
            upgrade_pullrequests=upgrade_pullrequests,
        ):
            logger.info(f'upgrade-pullrequest for {upgrade_vector=} already exists (skipping)')
            continue

        yield create_upgrade_pullrequest(
            upgrade_vector=upgrade_vector,
            component=component,
            component_descriptor_lookup=component_descriptor_lookup,
            version_lookup=version_lookup,
            github_api_lookup=github_api_lookup,
            repo_dir=repo_dir,
            repo_url=repo_url,
            repository=repository,
            auto_merge=auto_merge,
            merge_method=merge_method,
            branch=branch,
            oci_client=oci_client,
        )


def main():
    pass


if __name__ == '__main__':
    main()
