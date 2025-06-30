import dataclasses

import oci.model
import ocm


@dataclasses.dataclass
class ReplicationResourceOptions:
    extra_tags: list[str] = dataclasses.field(default_factory=list)
    remove_files: list[str] = dataclasses.field(default_factory=list)
    digest: str | None = None
    reference_by_digest: bool = False
    retain_symbolic_tag: bool = False
    convert_to_relative_ref: bool = False


@dataclasses.dataclass(kw_only=True)
class ReplicationResourceElement(ReplicationResourceOptions):
    source: ocm.Resource
    target: ocm.Resource
    component_id: ocm.ComponentIdentity

    @property
    def src_ref(self) -> oci.model.OciImageReference:
        if self.source.access.type is ocm.AccessType.OCI_REGISTRY:
            return oci.model.OciImageReference(self.source.access.imageReference)
        elif self.source.access.type is ocm.AccessType.RELATIVE_OCI_REFERENCE:
            return oci.model.OciImageReference(self.source.access.reference, normalise=False)
        else:
            raise ValueError(self.source.access.type)

    @property
    def tgt_ref(self) -> oci.model.OciImageReference:
        if self.target.access.type is ocm.AccessType.OCI_REGISTRY:
            return oci.model.OciImageReference(self.target.access.imageReference)
        elif self.target.access.type is ocm.AccessType.RELATIVE_OCI_REFERENCE:
            return oci.model.OciImageReference(self.target.access.reference, normalise=False)
        else:
            raise ValueError(self.source.access.type)

    @property
    def preliminary_tgt_ref(self) -> oci.model.OciImageReference:
        '''
        In case the resource does not exist in the target yet, we don't know the digest for sure
        (for example, there might be filtering). To show a reasonable output in the replication plan
        nonetheless, use a placeholder digest in case no digest can be determined to show that the
        replicated resource will contain a digest tag.
        '''
        if not self.reference_by_digest:
            return self.tgt_ref

        tgt_ref = self.tgt_ref

        if self.digest:
            digest = self.digest
        elif tgt_ref.has_digest_tag:
            digest = self.tgt_ref.tag
        else:
            digest = 'sha256:<placeholder-digest>'

        if (
            self.retain_symbolic_tag
            and (tgt_ref.has_symbolical_tag or tgt_ref.has_mixed_tag)
        ):
            return oci.model.OciImageReference(f'{tgt_ref.with_symbolical_tag}@{digest}')
        else:
            return oci.model.OciImageReference(f'{tgt_ref.ref_without_tag}@{digest}')

    def __str__(self) -> str:
        return (
            f'{self.source.name}:{self.source.version} '
            f'[{self.src_ref} -> {self.preliminary_tgt_ref}]'
        )


@dataclasses.dataclass
class ReplicationComponentElement:
    source: ocm.ComponentDescriptor
    target: ocm.ComponentDescriptor

    @property
    def src_ref(self) -> str:
        component = self.source.component
        return component.repositoryContexts[-1].component_version_oci_ref(component)

    @property
    def tgt_ref(self) -> str:
        component = self.target.component
        return component.repositoryContexts[-1].component_version_oci_ref(component)

    def __str__(self) -> str:
        return (
            f'{self.source.component.name}:{self.target.component.version} '
            f'[{self.src_ref} -> {self.tgt_ref}]'
        )


@dataclasses.dataclass
class ReplicationPlanStep:
    '''
    To each target registry, _all_ OCM component descriptors are replicated (independent of
    configuration) as well as their respective OCI resources (based on the `processing.cfg`).
    The replication of the `resources` can be done in parallel, however, the OCM component
    descriptors must be replicated sequentially to allow early-exiting in case a root component
    descriptor already exists in the target registry.
    '''
    target_ocm_repository: str
    resources: tuple[ReplicationResourceElement]
    components: tuple[ReplicationComponentElement]

    def __str__(self) -> str:
        resources = (
            resource
            for resource in sorted(
                self.resources,
                key=lambda resource: (resource.source.name, resource.source.version),
            ) if not resource.digest # only show resources which don't exist in target yet
        )

        return f'''\
- **{self.target_ocm_repository}**

  1. Replication of OCI resources (processed in-parallel)

{
    '\n'.join(
        f'    - {resource}'
        for resource in resources
    ) or '    None'
}

  2. Replication of OCM component descriptors (processed sequentially)

{
    '\n'.join(
        f'    {idx + 1}. {component}'
        for idx, component in enumerate(self.components)
    ) or '    None'
}
'''


@dataclasses.dataclass
class ReplicationPlan:
    '''
    Contains all information required for replication. The described `steps` can be processed in
    parallel. Each `step` describes the replication to a different target registry.
    '''
    steps: list[ReplicationPlanStep] = dataclasses.field(default_factory=list)

    def __str__(self) -> str:
        return f'''\
**Replication Plan**

Targets: {[step.target_ocm_repository for step in self.steps]}

{'\n'.join(str(step) for step in self.steps)}
'''
