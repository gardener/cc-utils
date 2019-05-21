import container.registry
import util


def republish_image(
    src_ref,
    tgt_prefix,
    mangle=True,
):
    img_ref, tag = src_ref.rsplit(':', 1)
    if mangle:
        img_ref = img_ref.replace('.', '_')

    tgt_ref = util.urljoin(tgt_prefix, ':'.join((img_ref, tag)))

    fh = container.registry.retrieve_container_image(image_reference=src_ref)
    container.registry.publish_container_image(image_reference=tgt_ref, image_file_obj=fh)
    return src_ref, tgt_ref
