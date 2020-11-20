import json
import typing

import dacite

import oci._util as _ou
import oci.auth as oa
import oci.model as om
import oci.util as ou


# type-alias for typehints
image_reference = str


def image_exists(
    image_reference: str,
    credentials_lookup: typing.Callable[[image_reference, oa.Privileges], oa.OciConfig],
) -> bool:
    '''
    returns a boolean value indicating whether or not the given OCI Artifact exists
    '''
    transport = _ou._mk_transport_pool(size=1)

    image_reference = ou.normalise_image_reference(image_reference=image_reference)
    image_reference = _ou.docker_name.from_string(image_reference)
    creds = _ou._mk_credentials(
        image_reference=image_reference,
        credentials_lookup=credentials_lookup,
    )

    # keep import local to avoid exposure to module's users
    from containerregistry.client.v2_2 import docker_image_list as image_list

    with image_list.FromRegistry(image_reference, creds, transport) as img_list:
        if img_list.exists():
            return True

    # keep import local to avoid exposure to module's users
    from containerregistry.client.v2_2 import docker_image as v2_2_image

    accept = _ou.docker_http.SUPPORTED_MANIFEST_MIMES
    with v2_2_image.FromRegistry(image_reference, creds, transport, accept) as v2_2_img:
        if v2_2_img.exists():
            return True

    return False


def retrieve_manifest(
    image_reference: str,
    absent_ok: bool=False,
) -> om.OciImageManifest:
  try:
    raw_dict = json.loads(
        _retrieve_raw_manifest(
            image_reference=image_reference,
            absent_ok=False,
        )
    )
    manifest = dacite.from_dict(
      data_class=om.OciImageManifest,
      data=raw_dict,
    )

    return manifest
  except om.OciImageNotFoundException as oie:
    if absent_ok:
      return None
    raise oie
