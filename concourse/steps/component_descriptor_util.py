import ci.util
import logging
import os

import gci
import gci.componentmodel as cm

logger: logging.Logger = logging.getLogger(__name__)


def component_descriptor_fname(
    schema_version=cm.SchemaVersion.V2,
):
    if schema_version is cm.SchemaVersion.V2:
      return 'component_descriptor_v2'
    else:
        raise NotImplementedError(schema_version)


def component_descriptor_path(
    schema_version=cm.SchemaVersion.V2,
):
    fname = component_descriptor_fname(schema_version=schema_version)
    return os.path.join(
      ci.util.check_env('COMPONENT_DESCRIPTOR_DIR'),
      fname,
    )


def parse_component_descriptor(
    schema_version=cm.SchemaVersion.V2,
):
    if schema_version is cm.SchemaVersion.V2:
      component_descriptor = cm.ComponentDescriptor.from_dict(
          component_descriptor_dict=ci.util.parse_yaml_file(
            component_descriptor_path(
              schema_version=schema_version,
            )
          )
      )
      return component_descriptor
    else:
        raise NotImplementedError(schema_version)


def component_descriptor_from_component_descriptor_path(
    cd_path: str,
) -> gci.componentmodel.ComponentDescriptor:

    if not os.path.isfile(cd_path):
        raise FileNotFoundError(
            f'{os.path.abspath(cd_path)=} not found'
        )

    descriptor_v2 = gci.componentmodel.ComponentDescriptor.from_dict(
        ci.util.parse_yaml_file(cd_path)
    )
    logger.info(f'found component-descriptor (v2) at {cd_path=}')
    return descriptor_v2


def component_descriptor_from_dir(dir_path: str) -> gci.componentmodel.ComponentDescriptor:
    if not os.path.isdir(dir_path):
        raise NotADirectoryError(
            f'{os.path.abspath(dir_path)=} is no directory'
        )

    component_descriptor_path = os.path.join(
        dir_path,
        component_descriptor_fname(
            schema_version=gci.componentmodel.SchemaVersion.V2,
        ),
    )

    have_cd = os.path.exists(component_descriptor_path)

    if have_cd:
        return component_descriptor_from_component_descriptor_path(
            cd_path=component_descriptor_path,
        )
    else:
        print(f'did not find expected component-descriptor at {component_descriptor_path=}')
        exit(1)
