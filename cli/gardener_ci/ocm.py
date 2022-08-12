import gci.componentmodel as cm

import cnudie.retrieve
import cnudie.iter
import ctx

_cfg = ctx.cfg


def traverse(
    name: str,
    version: str,
    ctx_base_url: str=None,
):
    if not ctx_base_url:
        ctx_base_url = _cfg.ctx.ocm_repo_base_url

    ctx_repo = cm.OciRepositoryContext(
            baseUrl=ctx_base_url,
            componentNameMapping=cm.OciComponentNameMapping.URL_PATH,
    )

    component_descriptor = cnudie.retrieve.component_descriptor(
        name=name,
        version=version,
        ctx_repo=ctx_repo,
    )
    component = component_descriptor.component

    lookup = cnudie.iter.dictbased_lookup(
        components=cnudie.retrieve.components(
            component=component,
        )
    )

    for node in cnudie.iter.iter(
        component=component,
        lookup=lookup,
    ):
        indent = len(node.path * 2)
        if isinstance(node, cnudie.iter.ComponentNode):
            prefix = 'c'
            print(f'{prefix}{" " * indent}{node.component.name}')
        if isinstance(node, cnudie.iter.ResourceNode):
            prefix = 'r'
            indent += 1
            print(f'{prefix}{" " * indent}{node.resource.name}')
        if isinstance(node, cnudie.iter.SourceNode):
            prefix = 'r'
            indent += 1
            print(f'{prefix}{" " * indent}{node.source.name}')
