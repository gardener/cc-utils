import sys

import ccc.oci
import gci.componentmodel as cm
import product.v2
import version


def retrieve(
    name: str,
    version: str,
    ctx_base_url: str=None,
    out: str=None
):
    ctx_repo = cm.OciRepositoryContext(
            baseUrl=ctx_base_url,
            componentNameMapping=cm.OciComponentNameMapping.URL_PATH,
        )

    target_ref = product.v2._target_oci_ref_from_ctx_base_url(
        component_name=name,
        component_version=version,
        ctx_repo=ctx_repo,
    )

    component_descriptor = product.v2.retrieve_component_descriptor_from_oci_ref(
        manifest_oci_image_ref=target_ref,
        absent_ok=False,
    )

    if out:
        outfh = open(out, 'w')
    else:
        outfh = sys.stdout

    component_descriptor.to_fobj(fileobj=outfh)
    outfh.flush()
    outfh.close()


def ls(
    name: str,
    greatest: bool=False,
    ctx_base_url: str=None,
):
    oci_name = product.v2._target_oci_repository_from_component_name(
        component_name=name,
        ctx_repo_base_url=ctx_base_url,
    )
    client = ccc.oci.oci_client()
    tags = client.tags(image_reference=oci_name)
    if greatest:
        print(version.greatest_version(tags))
    else:
        print(tags)
