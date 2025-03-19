#!/usr/bin/env python

import os
import sys
# for local development
sys.path.insert(
    1,
    os.path.join(
        os.path.dirname(__file__),
        '../../..',
    ),
)

# flake8: noqa: E402
import collections
import dataclasses
import datetime
import hashlib
import json

import jsonpath_ng
import yaml

import oci.client
import oci.model
import ocm


# copy-pasted from concourse/model/traits/publish.py
# (as Concourse and GHAs are not planned to be kept in-sync, it seems more adequate to create a
#  copy, rather then re-using)
@dataclasses.dataclass
class HelmchartValueMapping:
    '''
    a mapping between OCM-Resources and values to be passed to a helmchart.

    Typically, this will be a reference to an OCI Image mapped to attributes within a
    "values.yaml" for helm.

    Mappings are evaluated as preprocessing step before creating
    helmchart-archive (i.e. values.yaml is patched). They are also incorporated into resulting
    OCM Component-Descriptors.

    ref: reference to (virtual) attribute of an OCM-Resource
    attribute: JSONPath-spec

    Syntax for ref:

        oci-resource:<name>.<attribute>
        ^^^^^
        prefix

        name: resource-name (match names used in `dockerimages` attribute)
        attribute: choose either of:
            repository: image-reference without tag
            tag:        tag (either symbolic tag, or digest)
            digest:     image-digest (including algorithm-prefix, e.g. sha256:...)
            image:      image-reference including tag

    Example:

    Assuming there is a component-descriptor with an OciImage-Resource named `my-image` that
    should be mapped to attributes `image.repository` and `image.tag` in values.yaml.

    - ref: ocm-resource:my-image.repository
      attribute: image.repository
    - ref: ocm-resource:my-image.tag
      attribute: image.tag
    '''
    ref: str
    attribute: str

    @property
    def referenced_resource_and_attribute(self):
        '''
        returns a two-tuple of reference resource-name and resource's attribute, parsed from
        `ref`. If ref is not a valid ref (see class-docstr), raises ValueError.
        '''
        if not self.ref.startswith(prefix := 'ocm-resource:'):
            raise ValueError(self.ref, f'must start with {prefix=}')

        ref = self.ref.removeprefix(prefix)
        parts = ref.split('.')
        if not len(parts) == 2:
            raise ValueError(ref, 'must consist of exactly two period-separated parts')

        resource_name, attribute_name = parts
        return resource_name, attribute_name


def find_resource(
    component: ocm.Component,
    name: str,
):
    for resource in component.resources:
        if not resource.type is ocm.ArtefactType.OCI_IMAGE:
            continue
        if resource.name == name:
            return resource
    print(f'Error: did not find resource {name=} in component-descriptor')
    exit(1)


def patch_values_yaml(
    component: ocm.Component,
    values_yaml_path: str,
    mappings: list[HelmchartValueMapping],
):
    '''
    updates the given helm-values-file ("values.yaml") according to the given mappings. each
    mapping between a helm-value and a ocm-resource in the given component will be resolved,
    and written into the helm-values-file.
    '''
    with open(values_yaml_path) as f:
        values = yaml.safe_load(f)

    # guard against empty values-file - initialise as empty dict
    # this will allow us to patch-in values + ensure upload as cfg-blob (part of oci-artefact)
    # will succeed (at least our OCI-Client cannot properly handle uploading empty blobs)
    if values is None:
        values = {}
        with open(values_yaml_path, 'w') as f:
            yaml.safe_dump(values, f)

    for mapping in mappings:
        image_name, image_attr = mapping.referenced_resource_and_attribute
        resource = find_resource(
            component=component,
            name=image_name,
        )

        image_ref = oci.model.OciImageReference(resource.access.imageReference)

        if image_attr == 'repository':
            value = image_ref.ref_without_tag
        elif image_attr == 'tag':
            value = image_ref.tag
        elif image_attr == 'image':
            value = str(image_ref)
        else:
            print('Unexpected {image_attr=} (expected repository, tag, image)')
            exit(1)

        attribute = mapping.attribute
        attribute_path = jsonpath_ng.parse(attribute)

        attribute_path.update_or_create(values, value)


def to_ocm_mapping(
    helmchart_name: str,
    mappings: list[HelmchartValueMapping],
) -> dict:
    '''
    converts the given `mapping` into a mapping-dict compliant to the proposed one from:
    https://github.com/open-component-model/ocm/blob/2b9ed814dee16e351636cb0d4ea0203f72224c0d/components/helmdemo/README.md
    '''
    attrs_by_resource = collections.defaultdict(dict)
    for mapping in mappings:
        resource_name, ref_name = mapping.referenced_resource_and_attribute
        attrs_by_resource[resource_name][ref_name] = mapping.attribute

    image_mappings = []

    for resource_name, attribute_mappings in attrs_by_resource.items():
        image_mapping = {
            'resource': {'name': resource_name},
        }
        for ref, attr in attribute_mappings.items():
            image_mapping[ref] = attr

        image_mappings.append(image_mapping)

    return {
        'helmchartResource': {
            'name': helmchart_name,
        },
        'imageMapping': image_mappings,
    }


def upload_helmchart(
    helmchart_archive_path: str,
    helmchart_name: str,
    helmchart_description: str,
    version: str,
    helm_values_path: str,
    target_ref: oci.model.OciImageReference,
    oci_client: oci.client.Client,
) -> ocm.Resource:
    with open(helmchart_archive_path, 'rb') as f:
        sha256 = hashlib.sha256()
        leng = 0
        while (chunk := f.read(4096)):
            leng += len(chunk)
            sha256.update(chunk)

        f.seek(0)

        oci_client.put_blob(
            image_reference=target_ref,
            digest=(digest := f'sha256:{sha256.hexdigest()}'),
            octets_count=leng,
            data=f,
        )

    with open(helm_values_path, 'rb') as f:
        helm_values = yaml.safe_load(f) or {} # guard against empty values-files

    cfg_blob_bytes = json.dumps(helm_values).encode('utf-8')
    cfg_blob_digest = f'sha256:{hashlib.sha256(cfg_blob_bytes).hexdigest()}'
    cfg_blob_leng = len(cfg_blob_bytes)

    oci_client.put_blob(
        image_reference=target_ref,
        digest=cfg_blob_digest,
        octets_count=cfg_blob_leng,
        data=cfg_blob_bytes,
    )

    isonow = datetime.datetime.now(tz=datetime.timezone.utc).isoformat(timespec='seconds')
    isonow = isonow.replace('+00:00', 'Z') # match format used by helm

    manifest = oci.model.OciImageManifest(
        annotations={
          'org.opencontainers.image.created': isonow,
          'org.opencontainers.image.description': helmchart_description,
          'org.opencontainers.image.title': helmchart_name,
          'org.opencontainers.image.version': version,
        },
        config=oci.model.OciBlobRef(
            digest=cfg_blob_digest,
            mediaType='application/vnd.cncf.helm.config.v1+json',
            size=cfg_blob_leng,
        ),
        layers=[
            oci.model.OciBlobRef(
                digest=digest,
                mediaType='application/vnd.cncf.helm.chart.content.v1.tar+gzip',
                size=leng,
            ),
        ],
    )

    oci_client.put_manifest(
        image_reference=target_ref,
        manifest=json.dumps(manifest.as_dict()).encode('utf-8'),
    )

    print(f'published helmchart to {target_ref=}')

    return ocm.Resource(
      name=helmchart_name,
      version=version,
      type='helmChart',
      extraIdentity={
        'type': 'helmChart', # avoid clashes w/ equally-named images (or other resources)
      },
      relation=ocm.ResourceRelation.LOCAL,
      access=ocm.OciAccess(
        type=ocm.AccessType.OCI_REGISTRY,
        imageReference=str(target_ref),
      ),
    )


def patch_helmchart_name(
    chart_yaml_path: str,
    name: str,
):
    with open(chart_yaml_path) as f:
        chart = yaml.safe_load(f)

    if chart.get('name') == name:
        return

    print(f'{chart_yaml_path}\'s `name` differs from {name=} - patching')
    chart['name'] = name
    with open(chart_yaml_path, 'w') as f:
        yaml.safe_dump(chart, f)
