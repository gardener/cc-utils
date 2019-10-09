import ci.util
import os

import concourse.model.traits.component_descriptor
import product.model


def parse_component_descriptor():
    component_descriptor_file = os.path.join(
      ci.util.check_env(
        ci.util.sane_env_var_name(concourse.model.traits.component_descriptor.ENV_VAR_NAME)
      ),
      'component_descriptor'
    )

    component_descriptor = product.model.ComponentDescriptor.from_dict(
      raw_dict=ci.util.parse_yaml_file(component_descriptor_file)
    )
    return component_descriptor
