import ocm


def upgrade_pullrequest_title(
    reference: ocm.ComponentReference,
    from_version: str,
    to_version: str,
) -> str:
    if not isinstance(reference, ocm.ComponentReference):
        raise TypeError(reference)

    type_name = 'component'
    reference_name = reference.componentName

    return f'[ci:{type_name}:{reference_name}:{from_version}->{to_version}]'
