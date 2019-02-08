import container.util
import util


def filter_image(
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
        util.warn('no files to remove were specified - the output will remain unaltered')

    container.util.filter_container_image(
        image_file=in_file,
        out_file=out_file,
        remove_entries=remove_files,
    )
