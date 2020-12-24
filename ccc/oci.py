import functools

import oci.auth as oa
import oci.client as oc
import model.container_registry


@functools.lru_cache
def oci_cfg_lookup():
    def find_credentials(
        image_reference: str,
        privileges: oa.Privileges=oa.Privileges.READONLY,
        absent_ok: bool=True,
    ):
        registry_cfg = model.container_registry.find_config(
            image_reference=image_reference,
            privileges=privileges,
        )
        if not registry_cfg:
            return None # fallback to docker-cfg (or try w/o auth)
        creds = registry_cfg.credentials()
        return oa.OciBasicAuthCredentials(
            username=creds.username(),
            password=creds.passwd(),
        )

    return find_credentials


def oci_client(credentials_lookup=oci_cfg_lookup()):
    return oc.Client(credentials_lookup=credentials_lookup)
