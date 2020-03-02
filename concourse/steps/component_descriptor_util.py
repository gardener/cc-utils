import ci.util
import os

import product.model


def component_descriptor_path():
    return os.path.join(
      ci.util.check_env('COMPONENT_DESCRIPTOR_DIR'),
      'component_descriptor'
    )


def parse_component_descriptor():
    component_descriptor = product.model.ComponentDescriptor.from_dict(
      raw_dict=ci.util.parse_yaml_file(component_descriptor_path())
    )
    return component_descriptor
