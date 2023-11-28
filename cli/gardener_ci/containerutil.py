import ccc.oci
import container.util
import oci


def filter_image(
    source_ref:str,
    target_ref:str,
    remove_files:[str]=[],
):
    container.util.filter_image(
        source_ref=source_ref,
        target_ref=target_ref,
        remove_files=remove_files,
        mode=oci.ReplicationMode.PREFER_MULTIARCH,
    )


def to_digest_ref(image_ref:str):
    oci_client = ccc.oci.oci_client()
    print(oci_client.to_digest_hash(image_reference=image_ref))
