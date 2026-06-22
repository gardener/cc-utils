# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
'''
SBOM/CBOM support for OCI images and OCM components.

Sub-modules:
  sbom.oci     — OCI 1.1 referrer push/lookup mechanics and media-type constants
  sbom.cbom    — OCI referrer mechanics and OCM resource construction for CBOM documents
  sbom.inject  — syft/cbomkit-theia scanning, resource-aware injection, OCM resource construction
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
from sbom.cbom import (  # noqa: F401
    CBOM_ARTIFACT_TYPE,
    CBOM_LAYER_MEDIA_TYPE,
    push_cbom_referrer,
    build_cbom_ocm_resources,
)
from sbom.iter import (  # noqa: F401
    SbomSource,
    SbomMapping,
    iter_sboms_for_resource,
    iter_sboms,
    fetch_sbom_document,
)
