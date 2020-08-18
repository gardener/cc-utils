import ci.util
import os

import gci.componentmodel

import product.model


def component_descriptor_fname(
    schema_version=gci.componentmodel.SchemaVersion.V1,
):
    if schema_version is gci.componentmodel.SchemaVersion.V1:
      return 'component_descriptor'
    elif schema_version is gci.componentmodel.SchemaVersion.V2:
      return 'component_descriptor_v2'
    else:
        raise NotImplementedError(schema_version)


def component_descriptor_path(
    schema_version=gci.componentmodel.SchemaVersion.V1,
):
    fname = component_descriptor_fname(schema_version=schema_version)
    return os.path.join(
      ci.util.check_env('COMPONENT_DESCRIPTOR_DIR'),
      fname,
    )


def parse_component_descriptor(
    schema_version=gci.componentmodel.SchemaVersion.V1,
):
    if schema_version is gci.componentmodel.SchemaVersion.V1:
      component_descriptor = product.model.ComponentDescriptor.from_dict(
        raw_dict=ci.util.parse_yaml_file(
          component_descriptor_path(
            schema_version=schema_version,
          )
        )
      )
      return component_descriptor
    elif schema_version is gci.componentmodel.SchemaVersion.V2:
      component_descriptor = gci.componentmodel.ComponentDescriptor.from_dict(
          component_descriptor_dict=ci.util.parse_yaml_file(
            component_descriptor_path(
              schema_version=schema_version,
            )
          )
      )
      return component_descriptor
    else:
        raise NotImplementedError(schema_version)
