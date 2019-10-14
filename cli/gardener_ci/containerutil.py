import ci.util
import container.registry
import container.util


def filter_image_file(
    in_file:str,
    out_file:str,
    remove_files:[str]=[],
):
    '''
    processes an OCI container image [0] (from a local tar file) and writes a
    modified copy to the specified `out_file`.

    All files (specified as absolute paths w/o loading slash (/)) are removed from
    all layer archives. Contained metadata is updated accordingly.

    [0] https://github.com/opencontainers/image-spec
    '''
    if not remove_files:
        ci.util.warning('no files to remove were specified - the output will remain unaltered')

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
    container.util.filter_image(
        source_ref=source_ref,
        target_ref=target_ref,
        remove_files=remove_files,
    )
