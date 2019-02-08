import tempfile

import container.registry
import container.util
import util


def filter_image_file(
    in_file:str,
    out_file:str,
    remove_files:[str]=(),
):
    '''
    processes an OCI container image [0] (from a local tar file) and writes a
    modified copy to the specified `out_file`.

    All files (specified as absolute paths w/o loading slash (/)) are removed from
    all layer archives. Contained metadata is updated accordingly.

    [0] https://github.com/opencontainers/image-spec
    '''
    if not remove_files:
        util.warning('no files to remove were specified - the output will remain unaltered')

    container.util.filter_container_image(
        image_file=in_file,
        out_file=out_file,
        remove_entries=remove_files,
    )


def filter_image(
    source_ref:str,
    target_ref:str,
    remove_files:[str]=[],
):
    with tempfile.NamedTemporaryFile() as in_fh:
        container.registry.retrieve_container_image(image_reference=source_ref, outfileobj=in_fh)

        # XXX enable filter_image_file / filter_container_image to work w/o named files
        with tempfile.NamedTemporaryFile() as out_fh:
            filter_image_file(
                in_file=in_fh.name,
                out_file=out_fh.name,
                remove_files=remove_files
            )

            container.registry.publish_container_image(
                image_reference=target_ref,
                image_file_obj=out_fh,
            )
