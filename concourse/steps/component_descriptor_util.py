import os
import ci.util

import product.model


def parse_component_descriptor():
    component_descriptor_file = os.path.join(
      ci.util.check_env('COMPONENT_DESCRIPTOR_DIR'),
      'component_descriptor'
    )

    component_descriptor = product.model.ComponentDescriptor.from_dict(
      raw_dict=ci.util.parse_yaml_file(component_descriptor_file)
    )
    return component_descriptor
