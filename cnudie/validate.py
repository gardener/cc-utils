import collections.abc
import dataclasses
import logging

import ocm
import ocm.iter as oi
import oci.model

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class ValidationError:
    node: oi.Node
    error: str

    @property
    def as_error_message(self):
        def _node_id(node: oi.Node | ocm.Component):
            if isinstance(node, oi.ComponentNode):
                return f'{node.component.name}:{node.component.version}'
            elif isinstance(node, ocm.Component):
                component = node
                return f'{component.name}:{component.version}'
            elif isinstance(node, oi.ResourceNode):
                artefact = node.resource
            elif isinstance(node, oi.SourceNode):
                artefact = node.source
            else:
                raise TypeError(node)

            return f'{artefact.name}:{artefact.version}'

        node_id_path = '/'.join((_node_id(node) for node in self.node.path))

        return f'{node_id_path}: {self.error}'


def _validate_resource_node(
    node: oi.ResourceNode,
    oci_client: oci.client.Client,
) -> ValidationError | None:
    resource = node.resource
    if resource.type != ocm.ArtefactType.OCI_IMAGE:
        return

    access: ocm.OciAccess = resource.access
    image_reference = access.imageReference

    try:
        image_reference = oci.model.OciImageReference.to_image_ref(image_reference)
        if not image_reference.has_tag:
            return ValidationError(
                node=node,
                error=f'Invalid ImageReference (missing tag): {image_reference}',
            )
    except ValueError:
        # cannot perform checks in image itself using invalid image-ref
        return ValidationError(
            node=node,
            error=f'Invalid ImageReference: {image_reference}',
        )

    if not oci_client.head_manifest(
        image_reference=image_reference,
        absent_ok=True,
        accept=oci.model.MimeTypes.prefer_multiarch,
    ):
        return ValidationError(
            node=node,
            error=f'{image_reference=} does not exist',
        )


def iter_violations(
    nodes: collections.abc.Iterable[oi.Node],
    oci_client: oci.client.Client,
) -> collections.abc.Generator[ValidationError, None, None]:
    for node in nodes:
        if isinstance(node, oi.ComponentNode):
            continue # no validation, yet
        elif isinstance(node, oi.SourceNode):
            continue # no validation, yet
        elif isinstance(node, oi.ResourceNode):
            if validation_error := _validate_resource_node(
                node=node,
                oci_client=oci_client,
            ):
                yield validation_error
        else:
            raise ValueError(node)
