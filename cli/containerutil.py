import container.util


def filter_image(
    in_file:str,
    out_file:str,
    filters:[str]=['foo'],
):
    container.util.filter_container_image(
        image_file=in_file,
        out_file=out_file,
        remove_entries=filters,
    )
