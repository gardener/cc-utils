import collections.abc
import dataclasses
import enum
import json
import logging
import os
import typing

import dacite
import jsonschema
import yaml

import ocm
import ocm.iter as oi
import oci.model

logger = logging.getLogger(__name__)

own_dir = os.path.dirname(__file__)
ocm_jsonschema_path = os.path.join(own_dir, 'ocm-component-descriptor-schema.yaml')


class ValidationMode(enum.StrEnum):
    '''
    specifies how validation should be done:

    SKIP: validation will be skipped (especially helpful for checks with runtime-requirements, such
          as `access`-checking)
    WARN: violations will be signalled as a warning
    FAIL: violations will be signalled as an error
    '''
    SKIP = 'skip'
    WARN = 'warn'
    FAIL = 'fail'


class ValidationType(enum.StrEnum):
    SCHEMA = 'schema'
    ACCESS = 'access'
    ARTEFACT_UNIQUENESS = 'artefact-uniqueness'


@dataclasses.dataclass
class ValidationCfg:
    '''
    specifies which kinds of validations should be done, and how they should be handled
    '''
    schema: ValidationMode
    access: ValidationMode
    artefact_uniqueness: ValidationMode

    @staticmethod
    def from_dict(cfg: dict, /) -> typing.Self:
        return dacite.from_dict(
            ValidationCfg,
            data=cfg,
            config=dacite.Config(
                cast=(enum.Enum,),
                convert_key=lambda k: k.replace('_', '-'),
            )
        )


@dataclasses.dataclass(kw_only=True)
class ValidationResult:
    mode: ValidationMode
    passed: bool
    node: oi.Node
    type: ValidationType

    @property
    def ok(self) -> bool:
        '''
        gives an indication as to whether this result is considered to be acceptable, depending on
        the used `ValidationMode`.
        '''
        if self.passed:
            return True
        if self.mode in (ValidationMode.WARN, ValidationMode.SKIP):
            return True
        return False


@dataclasses.dataclass(kw_only=True)
class ValidationError(ValidationResult):
    error: str

    @property
    def as_error_message(self):
        def node_id(node: oi.Node | ocm.Component):
            if isinstance(node, oi.ComponentNode):
                component = node.component.component
                return f'{component.name}:{component.version}'
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

        node_id_path = '/'.join(
            f'{node.component.component.name}:{node.component.component.version}'
            for node in self.node.path
        )

        if not isinstance(self.node, oi.ComponentNode):
            node_id_path = f'{node_id_path}/{node_id(self.node.component)}'

        return f'{node_id_path}: {self.error}'.removeprefix('/')


def iter_results_for_resource_node(
    node: oi.Node,
    validation_cfg: ValidationCfg,
    oci_client: oci.client.Client=None,
) -> collections.abc.Iterable[ValidationResult]:
    if validation_cfg.access is ValidationMode.SKIP:
        return

    resource = node.resource
    if resource.access.type is not ocm.AccessType.OCI_REGISTRY:
        yield ValidationResult(
            mode=validation_cfg.access,
            passed=True,
            node=node,
            type=ValidationType.ACCESS,
        )
        return

    access: ocm.OciAccess = resource.access
    image_reference = access.imageReference

    try:
        image_reference = oci.model.OciImageReference.to_image_ref(image_reference)
        if not image_reference.has_tag:
            yield ValidationError(
                node=node,
                mode=validation_cfg.access,
                passed=False,
                error=f'Invalid ImageReference (missing tag): {image_reference}',
                type=ValidationType.ACCESS,
            )
    except ValueError:
        # cannot perform checks in image itself using invalid image-ref
        yield ValidationError(
            passed=False,
            mode=validation_cfg.access,
            node=node,
            error=f'Invalid ImageReference: {image_reference}',
            type=ValidationType.ACCESS,
        )

    if not oci_client.head_manifest(
        image_reference=image_reference,
        absent_ok=True,
        accept=oci.model.MimeTypes.prefer_multiarch,
    ):
        yield ValidationError(
            passed=False,
            mode=validation_cfg.access,
            node=node,
            error=f'{image_reference=} does not exist',
            type=ValidationType.ACCESS,
        )


def iter_results_for_component_node(
    node: oi.Node,
    validation_cfg: ValidationCfg,
) -> collections.abc.Iterable[ValidationResult]:
    if validation_cfg.schema is not ValidationMode.SKIP:
        with open(ocm_jsonschema_path) as f:
            ocm_schema = yaml.safe_load(f)

        if isinstance(node.component, ocm.Component):
            component_descriptor = ocm.ComponentDescriptor(
                component=node.component,
                meta=ocm.Metadata(),
            )
        else:
            component_descriptor = node.component

        # convert into JSON-Serialisable dict
        component_descriptor = dataclasses.asdict(component_descriptor)
        component_descriptor = json.dumps(
            obj=component_descriptor,
            cls=ocm.EnumJSONEncoder,
        )
        component_descriptor = json.loads(component_descriptor)

        try:
            jsonschema.validate(
                instance=component_descriptor,
                schema=ocm_schema,
            )
            yield ValidationResult(
                mode=validation_cfg.schema,
                passed=True,
                node=node,
                type=ValidationType.SCHEMA,
            )
        except jsonschema.ValidationError as ve:
            yield ValidationError(
                mode=validation_cfg.schema,
                passed=False,
                node=node,
                error=ve.message,
                type=ValidationType.SCHEMA,
            )
    if validation_cfg.artefact_uniqueness is not ValidationMode.SKIP:
        def check_uniqueness(artefacts: list[ocm.Artifact], kind: str):
            duplicate_resources = []
            seen_ids = set()

            for idx, a in enumerate(artefacts):
                aid = a.identity(artefacts)
                if aid in seen_ids:
                    duplicate_resources.append(
                        f'{idx=}: {aid}'
                    )
                else:
                    seen_ids.add(aid)

            if duplicate_resources:
                return ValidationError(
                    mode=validation_cfg.artefact_uniqueness,
                    passed=False,
                    node=node,
                    error=f'Duplicate {kind}s:\n{"\n".join(duplicate_resources)}',
                    type=ValidationType.ARTEFACT_UNIQUENESS,
                )
            return ValidationResult(
                mode=validation_cfg.artefact_uniqueness,
                passed=True,
                node=node,
                type=ValidationType.SCHEMA,
            )
        yield check_uniqueness(
            artefacts=node.component.sources,
            kind='source',
        )
        yield check_uniqueness(
            artefacts=node.component.resources,
            kind='resource',
        )


def iter_results_for_node(
    node: oi.Node,
    validation_cfg: ValidationCfg,
    oci_client: oci.client.Client=None,
) -> collections.abc.Iterable[ValidationResult]:
    if isinstance(node, oi.ComponentNode):
        yield from iter_results_for_component_node(
            node=node,
            validation_cfg=validation_cfg,
        )
    elif isinstance(node, oi.SourceNode):
        return # no validation, yet
    elif isinstance(node, oi.ResourceNode):
        yield from iter_results_for_resource_node(
            node=node,
            validation_cfg=validation_cfg,
            oci_client=oci_client,
        )
    else:
        raise ValueError(node)


def iter_results(
    nodes: collections.abc.Iterable[oi.Node],
    validation_cfg: ValidationCfg,
    oci_client: oci.client.Client=None,
) -> collections.abc.Iterable[ValidationResult]:
    for node in nodes:
        yield from iter_results_for_node(
            node=node,
            validation_cfg=validation_cfg,
            oci_client=oci_client,
        )


def iter_violations(
    nodes: collections.abc.Iterable[oi.Node],
    oci_client: oci.client.Client,
    validation_cfg: ValidationCfg,
) -> collections.abc.Iterable[ValidationError]:
    for result in iter_results(
        nodes=nodes,
        validation_cfg=validation_cfg,
        oci_client=oci_client,
    ):
        if isinstance(result, ValidationError):
            yield result
