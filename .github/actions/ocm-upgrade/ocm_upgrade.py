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

import yaml

import cnudie.retrieve
import github.pullrequest
import oci.auth
import oci.client
import ocm.base_component
import ocm.gardener

logger = logging.getLogger(__name__)


def create_ocm_lookups(
    ocm_repositories: collections.abc.Iterable[str],
) -> tuple[
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

    return component_descriptor_lookup, version_lookup


def create_diff_in_base_component(
    upgrade_vector: ocm.gardener.UpgradeVector,
    repo_dir,
    rel_path='.ocm/base-component.yaml',
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
    repo_dir,
    rel_path,
):
    cmd_env = github.pullrequest.set_dependency_cmd_env(
        upgrade_vector=upgrade_vector,
        repo_dir=repo_dir,
    )

    subprocess.run(
        (os.path.join(repo_dir, rel_path),),
        check=True,
        env=cmd_env,
    )


def create_upgrade_pullrequest_diff(
    upgrade_vector: ocm.gardener.UpgradeVector,
    repo_dir: str,
):
    if create_diff_in_base_component(
        upgrade_vector=upgrade_vector,
        repo_dir=repo_dir,
        rel_path='.ocm/base-component.yaml',
    ):
        logger.info('created upgrade-diff in base-component')
        return True

    create_diff_using_callback(
        upgrade_vector=upgrade_vector,
        repo_dir=repo_dir,
        rel_path='.ci/set_dependency_version',
    )


def main():
    pass


if __name__ == '__main__':
    main()
