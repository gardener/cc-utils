import typing

import oci.auth as oa
import oci._util as _ou
import oci.util as ou

# type-alias for typehints
image_reference = str


def image_exists(
    image_reference: str,
    credentials_lookup: typing.Callable[[image_reference, oa.Privileges], oa.OciConfig],
):
    transport = _ou._mk_transport_pool(size=1)

    image_reference = ou.normalise_image_reference(image_reference=image_reference)
    image_reference = _ou.docker_name.from_string(image_reference)
    creds = _ou._mk_credentials(
        image_reference=image_reference,
        credentials_lookup=credentials_lookup,
    )

    with _ou.image_list.FromRegistry(image_reference, creds, transport) as img_list:
        if img_list.exists():
            return True

    accept = _ou.docker_http.SUPPORTED_MANIFEST_MIMES
    with _ou.v2_2_image.FromRegistry(image_reference, creds, transport, accept) as v2_2_img:
        if v2_2_img.exists():
            return True

    return False
