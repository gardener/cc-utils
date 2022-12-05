import dataclasses
import logging
import typing

import gci.componentmodel as cm

import ccc.oci
import cnudie.iter as ci
import oci.model

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class ValidationError:
    node: ci.Node
    error: str

    @property
    def as_error_message(self):
        def _node_id(node: ci.Node | cm.Component):
            if isinstance(node, ci.ComponentNode):
                return f'{node.component.name}{node.component.version}'
            elif isinstance(node, cm.Component):
                component = node
                return f'{component.name}{component.version}'
            elif isinstance(node, ci.ResourceNode):
                artefact = node.resource
            elif isinstance(node, ci.SourceNode):
                artefact = node.source
            else:
                raise TypeError(node)

            return f'{artefact.name}:{artefact.version}'

        node_id_path = '/'.join((_node_id(node) for node in self.node.path))

        return f'{node_id_path}: {self.error}'


def validate_resource_node(node: ci.ResourceNode) -> typing.Generator[ValidationError, None, None]:
    resource = node.resource
    if resource.type != cm.ResourceType.OCI_IMAGE:
        return

    resource.access: cm.OciAccess
    image_reference = resource.access.imageReference

    try:
        image_reference = oci.model.OciImageReference.to_image_ref(image_reference)
        if not image_reference.has_tag:
            yield ValidationError(
                node=node,
                error=f'Invalid ImageReference (missing tag): {image_reference}',
            )
            return
    except ValueError:
        yield ValidationError(
            node=node,
            error=f'Invalid ImageReference: {image_reference}',
        )
        return # cannot perform checks in image itself using invalid image-ref

    oci_client = ccc.oci.oci_client()

    if not oci_client.head_manifest(
        image_reference=image_reference,
        absent_ok=True,
    ):
        yield ValidationError(
            node=node,
            error=f'{image_reference=} does not exist',
        )


def iter_violations(
    nodes: typing.Iterable[ci.Node],
) -> typing.Generator[ValidationError, None, None]:
    for node in nodes:
        if isinstance(node, ci.ComponentNode):
            continue # no validation, yet
        elif isinstance(node, ci.SourceNode):
            continue # no validation, yet
        elif isinstance(node, ci.ResourceNode):
            yield from validate_resource_node(node=node)
        else:
            raise ValueError(node)
