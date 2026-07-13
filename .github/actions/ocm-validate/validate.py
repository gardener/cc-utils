import yaml

import cnudie.retrieve
import ocm
import ocm.iter
import ocm.validate

import oci.client


def validate(
    component_descriptor_path: str,
    validation_cfg: ocm.validate.ValidationCfg,
    ocm_repositories: str = '',
    recursion_depth: int = -1,
) -> tuple[int, list[ocm.validate.ValidationError], list[ocm.validate.ValidationError]]:
    with open(component_descriptor_path) as f:
        component_descriptor = yaml.safe_load(f)

    component_descriptor = ocm.ComponentDescriptor.from_dict(component_descriptor)

    if recursion_depth != 0:
        if not ocm_repositories:
            raise ValueError(
                '`ocm-repositories` must be provided when `recursion-depth` is non-zero'
            )
        repos = [r.strip() for r in ocm_repositories.split(',') if r.strip()]
        ocm_repo_lookup = cnudie.retrieve.ocm_repository_lookup(*repos)
        lookup = cnudie.retrieve.create_default_component_descriptor_lookup(
            ocm_repository_lookup=ocm_repo_lookup,
            oci_client=oci.client.client_with_dockerauth(),
        )
    else:
        lookup = None

    nodes = ocm.iter.iter(
        component=component_descriptor,
        lookup=lookup,
        recursion_depth=recursion_depth,
    )

    total = 0
    errors = []
    warnings = []

    for result in ocm.validate.iter_results(
        nodes=nodes,
        oci_client=oci.client.client_with_dockerauth(),
        validation_cfg=validation_cfg,
    ):
        total += 1
        if not isinstance(result, ocm.validate.ValidationError):
            continue

        if not result.ok:
            errors.append(result)
        else:
            warnings.append(result)

    return total, warnings, errors
