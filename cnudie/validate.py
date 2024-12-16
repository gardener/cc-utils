import collections.abc
import dataclasses
import logging

import ocm

import cnudie.iter as ci
import oci.model

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class ValidationError:
    node: ci.Node
    error: str

    @property
    def as_error_message(self):
        def _node_id(node: ci.Node | ocm.Component):
            if isinstance(node, ci.ComponentNode):
                return f'{node.component.name}:{node.component.version}'
            elif isinstance(node, ocm.Component):
                component = node
                return f'{component.name}:{component.version}'
            elif isinstance(node, ci.ResourceNode):
                artefact = node.resource
            elif isinstance(node, ci.SourceNode):
                artefact = node.source
            else:
                raise TypeError(node)

            return f'{artefact.name}:{artefact.version}'

        node_id_path = '/'.join((_node_id(node) for node in self.node.path))

        return f'{node_id_path}: {self.error}'


def _validate_resource_node(
    node: ci.ResourceNode,
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
    nodes: collections.abc.Iterable[ci.Node],
    oci_client: oci.client.Client,
) -> collections.abc.Generator[ValidationError, None, None]:
    for node in nodes:
        if isinstance(node, ci.ComponentNode):
            continue # no validation, yet
        elif isinstance(node, ci.SourceNode):
            continue # no validation, yet
        elif isinstance(node, ci.ResourceNode):
            if validation_error := _validate_resource_node(
                node=node,
                oci_client=oci_client,
            ):
                yield validation_error
        else:
            raise ValueError(node)
