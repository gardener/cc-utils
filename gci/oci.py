import dataclasses
import hashlib
import io
import logging
import os
import tarfile
import typing
import yaml

import ocm
import oci.client
import oci.model

logger = logging.getLogger(__name__)

component_descriptor_fname = 'component-descriptor.yaml'
# mimetype for component-descriptor-blobs (deprecated)
component_descriptor_mimetype = \
    'application/vnd.gardener.cloud.cnudie.component-descriptor.v2+yaml+tar'
component_descriptor_mimetypes = (
    component_descriptor_mimetype,
    'application/vnd.ocm.software.component-descriptor.v2+yaml+tar'
)
# mimetype for component-descriptor-oci-cfg-blobs
component_descriptor_cfg_mimetype = \
    'application/vnd.gardener.cloud.cnudie.component.config.v1+json'

dc = dataclasses.dataclass


@dc
class OciBlobRef:
    '''
    a single OCI registry layer reference as used in OCI Image Manifests
    '''
    digest: str
    size: int
    mediaType: str
    annotations: typing.Optional[typing.Dict] = None

    def as_dict(self) -> dict:
        raw = dataclasses.asdict(self)
        # fields that are None should not be included in the output
        raw = {k:v for k,v in raw.items() if v is not None}
        return raw


@dc
class ComponentDescriptorOciCfgBlobRef(OciBlobRef):
    mediaType: str = component_descriptor_cfg_mimetype


@dc
class ComponentDescriptorOciBlobRef(OciBlobRef):
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


def component_descriptor_from_tarfileobj(
    fileobj: io.BytesIO,
):
    with tarfile.open(fileobj=fileobj, mode='r') as tf:
        component_descriptor_info = tf.getmember(component_descriptor_fname)
        raw_dict = yaml.safe_load(tf.extractfile(component_descriptor_info).read())

        logger.debug(raw_dict)

        if raw_dict is None:
          raise ValueError('Component Descriptor appears to be empty')

        return ocm.ComponentDescriptor.from_dict(raw_dict)


def image_ref_with_digest(
    image_reference: str | oci.model.OciImageReference,
    digest: ocm.DigestSpec=None,
    oci_client: oci.client.Client=None,
) -> oci.model.OciImageReference:
    image_reference = oci.model.OciImageReference.to_image_ref(
        image_reference=image_reference,
    )

    if image_reference.has_digest_tag:
        return image_reference

    if not (digest and digest.value):
        if not oci_client:
            import ccc.oci # late import to avoid cyclic imports
            oci_client = ccc.oci.oci_client()

        digest = ocm.DigestSpec(
            hashAlgorithm=None,
            normalisationAlgorithm=None,
            value=hashlib.sha256(oci_client.manifest_raw(
                image_reference=image_reference,
                accept=oci.model.MimeTypes.prefer_multiarch,
            ).content).hexdigest(),
        )

    return oci.model.OciImageReference.to_image_ref(
        image_reference=image_reference.with_tag(tag=digest.oci_tag),
    )
