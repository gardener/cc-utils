import container.registry as cr

__cmd_name__ = 'oci'


def cp(src:str, tgt:str):
    cr.cp_oci_artifact(
        src_image_reference=src,
        tgt_image_reference=tgt,
    )
