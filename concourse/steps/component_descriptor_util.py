import os
import util

import product.model


def parse_component_descriptor():
    component_descriptor_file = os.path.join(
      util.check_env('COMPONENT_DESCRIPTOR_DIR'),
      'component_descriptor'
    )

    component_descriptor = product.model.Product.from_dict(
      raw_dict=util.parse_yaml_file(component_descriptor_file)
    )
    return component_descriptor
