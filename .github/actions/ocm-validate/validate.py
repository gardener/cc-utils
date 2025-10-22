import yaml

import ocm
import ocm.iter
import ocm.validate

import oci.client


def validate(
    component_descriptor_path: str,
    validation_cfg: ocm.validate.ValidationCfg,
) -> tuple[int, list[ocm.validate.ValidationError], list[ocm.validate.ValidationError]]:
    with open(component_descriptor_path) as f:
        component_descriptor = yaml.safe_load(f)

    component_descriptor = ocm.ComponentDescriptor.from_dict(component_descriptor)

    nodes = ocm.iter.iter(
        component=component_descriptor,
        lookup=None,
        recursion_depth=0,
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
