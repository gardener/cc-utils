import sys

import product.v2


def retrieve(
    name: str,
    version: str,
    ctx_base_url: str=None,
    out: str=None
):
    target_ref = product.v2._target_oci_ref_from_ctx_base_url(
        component_name=name,
        component_version=version,
        ctx_repo_base_url=ctx_base_url,
    )

    component_descriptor = product.v2.retrieve_component_descriptor_from_oci_ref(
        manifest_oci_image_ref=target_ref,
        absent_ok=False,
    )

    print(component_descriptor)

    if out:
        outfh = open(outfh)
    else:
        outfh = sys.stdout

    component_descriptor.to_fobj(fileobj=outfh)
    outfh.flush()
    outfh.close()
