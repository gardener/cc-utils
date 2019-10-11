import tempfile

import container.registry
import ci.util


def republish_image(
    src_ref,
    tgt_prefix,
    mangle=True,
):
    img_ref, tag = src_ref.rsplit(':', 1)
    if mangle:
        img_ref = img_ref.replace('.', '_')

    tgt_ref = ci.util.urljoin(tgt_prefix, ':'.join((img_ref, tag)))

    with tempfile.NamedTemporaryFile() as tmp_file:
        container.registry.retrieve_container_image(image_reference=src_ref, outfileobj=tmp_file)
        container.registry.publish_container_image(image_reference=tgt_ref, image_file_obj=tmp_file)

    return src_ref, tgt_ref
