'''
model-classes and utils for interaction with OCM-Component-Descriptors persisted in OCI Registries

Note: None of the Symbols defined in this module is intended as a stable API
      -> expect incompatible changes w/o prior notice
'''

import dataclasses
import io
import json
import logging
import os
import tarfile
import typing

import yaml

import ocm
import oci.model

logger = logging.getLogger(__name__)

component_descriptor_fname = 'component-descriptor.yaml'
# mimetype for component-descriptor-blobs (deprecated)
component_descriptor_mimetype = \
    'application/vnd.gardener.cloud.cnudie.component-descriptor.v2+yaml+tar'
component_descriptor_mimetypes = (
    component_descriptor_mimetype,
    'application/vnd.ocm.software.component-descriptor.v2+yaml+tar',
    'application/vnd.ocm.software.component-descriptor.v2+json',
)
# mimetype for component-descriptor-oci-cfg-blobs
component_descriptor_cfg_mimetype = \
    'application/vnd.gardener.cloud.cnudie.component.config.v1+json'

dc = dataclasses.dataclass


@dc
class ComponentDescriptorOciCfgBlobRef(oci.model.OciBlobRef):
    mediaType: str = component_descriptor_cfg_mimetype


@dc
class ComponentDescriptorOciBlobRef(oci.model.OciBlobRef):
    mediaType: str = component_descriptor_mimetype


@dc
class ComponentDescriptorOciCfg:
    '''
    a Component-Descriptor OCI configuration; it is used to store the reference to the
    (pseudo-)layer used to store the Component-Descriptor in
    '''
    componentDescriptorLayer: ComponentDescriptorOciCfgBlobRef


def component_descriptor_to_tarfileobj(
    component_descriptor: typing.Union[dict, ocm.ComponentDescriptor],
):
    if not isinstance(component_descriptor, dict):
        component_descriptor = dataclasses.asdict(component_descriptor)

    component_descriptor_buf = io.BytesIO(
        yaml.dump(
          data=component_descriptor,
          Dumper=ocm.EnumValueYamlDumper,
        ).encode('utf-8')
    )
    component_descriptor_buf.seek(0, os.SEEK_END)
    component_descriptor_leng = component_descriptor_buf.tell()
    component_descriptor_buf.seek(0)

    tar_buf = io.BytesIO()

    tf = tarfile.open(mode='w', fileobj=tar_buf)

    tar_info = tarfile.TarInfo(name=component_descriptor_fname)
    tar_info.size = component_descriptor_leng

    tf.addfile(tarinfo=tar_info, fileobj=component_descriptor_buf)
    tf.fileobj.seek(0)

    return tf.fileobj


def component_descriptor_from_blob(
    component_descriptor_blob: bytes,
    layer_mimetype: str,
    target_ref: str | None=None,
    component_id: ocm.ComponentIdentity | None=None,
) -> ocm.ComponentDescriptor:
    if not layer_mimetype in component_descriptor_mimetypes:
        logger.warning(f'{target_ref=} {layer_mimetype=} was unexpected')

    if '+tar' in layer_mimetype:
        try:
            with tarfile.open(fileobj=io.BytesIO(component_descriptor_blob), mode='r') as tf:
                component_descriptor_info = tf.getmember(component_descriptor_fname)
                component_descriptor_blob = tf.extractfile(component_descriptor_info).read()
        except tarfile.ReadError as tre:
            tre.add_note(f'{component_id=}')
            raise tre

    if '+yaml' in layer_mimetype:
        component_descriptor_dict = yaml.safe_load(component_descriptor_blob)
    elif '+json' in layer_mimetype:
        component_descriptor_dict = json.loads(component_descriptor_blob)
    else:
        raise ValueError(f'Unsupported component descriptor {layer_mimetype=}')

    logger.debug(component_descriptor_dict)

    if component_descriptor_dict is None:
        raise ValueError('Component Descriptor appears to be empty')

    return ocm.ComponentDescriptor.from_dict(component_descriptor_dict)
