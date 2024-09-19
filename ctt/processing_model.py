# SPDX-FileCopyrightText: 2021 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import dataclasses
import typing

import ocm


@dataclasses.dataclass
class ContainerImageUploadRequest:
    source_ref: str
    target_ref: str
    remove_files: typing.Sequence[str] = ()
    reference_target_by_digest: bool = False
    retain_symbolic_tag: bool = False


@dataclasses.dataclass
class ProcessingJob:
    component: ocm.Component
    resource: ocm.Resource
    upload_request: ContainerImageUploadRequest
    processed_resource: ocm.Resource | None = None  # added after re-upload
    inject_ocm_coordinates_into_oci_manifest: bool = False
    extra_tags: tuple[str] = ()
