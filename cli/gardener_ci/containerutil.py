import ccc.oci
import ci.util
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

    def parse_entries(remove_entries_files):
        for remove_entries_file in remove_entries_files:
            with open(remove_entries_file) as f:
                for l in f.readlines():
                    yield l.strip()

    remove_entries = [e for e in parse_entries(remove_files)]

    container.util.filter_container_image(
        image_file=in_file,
        out_file=out_file,
        remove_entries=remove_entries,
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


def to_digest_ref(image_ref:str):
    oci_client = ccc.oci.oci_client()
    print(oci_client.to_digest_hash(image_reference=image_ref))
