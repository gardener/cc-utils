import dataclasses
import pprint
import sys

import ccc.oci
import oci

__cmd_name__ = 'oci'


def cp(src:str, tgt:str):
    oci_client = ccc.oci.oci_client()

    oci.replicate_artifact(
        src_image_reference=src,
        tgt_image_reference=tgt,
        oci_client=oci_client,
    )


def ls(image: str):
    oci_client = ccc.oci.oci_client()

    print(oci_client.tags(image_reference=image))


def manifest(image_reference: str, pretty:bool=True):
    oci_client = ccc.oci.oci_client()

    if pretty:
        manifest = oci_client.manifest(image_reference=image_reference)

        pprint.pprint(dataclasses.asdict(manifest))
    else:
        manifest = oci_client.manifest_raw(image_reference=image_reference)
        print(manifest.text)


def cfg(image_reference: str):
    oci_client = ccc.oci.oci_client()

    manifest = oci_client.manifest(image_reference=image_reference)

    pprint.pprint(
        oci_client.blob(
            image_reference=image_reference,
            digest=manifest.config.digest,
            stream=False,
        ).json(),
    )


def blob(image_reference: str, digest: str, outfile: str):
    oci_client = ccc.oci.oci_client()

    if outfile == '-':
        if sys.stdout.isatty():
            print('must not stream binary content to stdout (pipe to other process)')
            exit(1)
        outfh = sys.stdout
        write = outfh.buffer.write
    else:
        outfh = open(outfile, 'wb')
        write = outfh.write

    blob = oci_client.blob(
        image_reference=image_reference,
        digest=digest,
        stream=True,
    )
    for chunk in blob.iter_content():
        write(chunk)

    outfh.flush()
