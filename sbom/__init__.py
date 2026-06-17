# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
'''
SBOM (Software Bill of Materials) support for OCI images and OCM components.

Sub-modules:
  sbom.oci     — OCI 1.1 referrer push/lookup mechanics and media-type constants
  sbom.inject  — syft-based scanning, resource-aware injection, OCM resource construction
  sbom.iter    — iterate and retrieve SBOM documents across OCM component trees
'''
from sbom.oci import (  # noqa: F401
    SPDX_JSON_MEDIA_TYPE,
    CYCLONEDX_JSON_MEDIA_TYPE,
    OCI_EMPTY_CONFIG_MEDIA_TYPE,
    SBOM_FORMATS,
    push_sbom_referrer,
    push_sbom_referrers,
)
from sbom.iter import (  # noqa: F401
    SbomSource,
    SbomMapping,
    iter_sboms_for_resource,
    iter_sboms,
    fetch_sbom_document,
)
