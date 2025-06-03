#!/usr/bin/env python

import collections.abc
import os
import sys

try:
    import ocm
except ImportError:
    # make local development more convenient
    repo_root = os.path.join(os.path.dirname(__file__), '../../..')
    sys.path.insert(1, repo_root)
    import ocm

import cnudie.retrieve
import oci.auth
import oci.client


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


def main():
    pass


if __name__ == '__main__':
    main()
