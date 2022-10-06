import sys

import cnudie.retrieve
import ctx
import gci.componentmodel as cm


def retrieve(
    name: str,
    version: str,
    ctx_base_url: str=None,
    out: str=None
):
    if not ctx_base_url:
        ctx_base_url = ctx.cfg.ctx.ocm_repo_base_url

    ctx_repo = cm.OciRepositoryContext(
            baseUrl=ctx_base_url,
            componentNameMapping=cm.OciComponentNameMapping.URL_PATH,
        )

    component_descriptor = cnudie.retrieve.oci_component_descriptor_lookup()(
        component_id=cm.ComponentIdentity(
            name=name,
            version=version,
        ),
        ctx_repo=ctx_repo,
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
    ocm_repo_base_url: str=None,
):
    if not ocm_repo_base_url:
        ocm_repo_base_url = ctx.cfg.ctx.ocm_repo_base_url

    ctx_repo = cm.OciRepositoryContext(baseUrl=ocm_repo_base_url)

    if greatest:
        print(cnudie.retrieve.greatest_component_version(
            component_name=name,
            ctx_repo=ctx_repo,
        ))
    else:
        print(cnudie.retrieve.component_versions(
            component_name=name,
            ctx_repo=ctx_repo,
        ))
