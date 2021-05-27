import ci.util
import logging
import os

import cnudie.util
import gci
import gci.componentmodel as cm
import product.v2


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


def component_descriptor_from_ctf_path(ctf_path: str) -> gci.componentmodel.ComponentDescriptor:

    if not os.path.exists(ctf_path):
        raise FileNotFoundError(
            f'{os.path.abspath(ctf_path)=} not found'
        )

    if not os.path.isfile(ctf_path):
        raise ValueError(
            f'{os.path.abspath(ctf_path)=} is not a file'
        )

    component_descriptors = [
        cd
        for cd in cnudie.util.component_descriptors_from_ctf_archive(ctf_path)
    ]

    if len(component_descriptors) == 0:
        raise RuntimeError(
            f'No component descriptor found in ctf archive at {os.path.abspath(ctf_path)}'
        )
    if len(component_descriptors) > 1:
        raise NotImplementedError(
            f'More than one component_descriptor found at {os.path.abspath(ctf_path)}'
        )
    logger.info(f'found component-descriptor (v2) in {ctf_path=}')
    return component_descriptors[0]


def component_descriptor_from_dir(dir_path: str) -> gci.componentmodel.ComponentDescriptor:

    if not os.path.isdir(dir_path):
        raise NotADirectoryError(
            f'{os.path.abspath(dir_path)=} is no directory'
        )

    v2_outfile = os.path.join(
        dir_path,
        component_descriptor_fname(
            schema_version=gci.componentmodel.SchemaVersion.V2,
        ),
    )
    ctf_out_path = os.path.abspath(
        os.path.join(
            dir_path,
            product.v2.CTF_OUT_DIR_NAME,
        )
    )

    have_ctf = os.path.exists(ctf_out_path)
    have_cd = os.path.exists(v2_outfile)

    if not have_ctf ^ have_cd:
        logger.error(f'exactly one of {ctf_out_path=}, {v2_outfile=} must exist')

    elif have_cd:
        return component_descriptor_from_component_descriptor_path(cd_path=v2_outfile)

    elif have_ctf:
        return component_descriptor_from_ctf_path(ctf_path=ctf_out_path)
