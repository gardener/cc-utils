import dataclasses
import enum
import hashlib
import pprint
import sys

import ccc.oci
import oci
import oci.model as om

__cmd_name__ = 'oci'


class OciManifestChoice(enum.Enum):
    PREFER_MULTIARCH = 'prefer-multiarch'
    SINGLE = 'single'
    MULTIARCH = 'multiarch'


def cp(src:str, tgt:str):
    oci_client = ccc.oci.oci_client()

    oci.replicate_artifact(
        src_image_reference=src,
        tgt_image_reference=tgt,
        oci_client=oci_client,
    )


def ls(image: str):
    oci_client = ccc.oci.oci_client()

    print('\n'.join(oci_client.tags(image_reference=image)))


def manifest(
    image_reference: str,
    pretty:bool=True,
    accept:OciManifestChoice=OciManifestChoice.SINGLE,
):
    oci_client = ccc.oci.oci_client()

    if accept is OciManifestChoice.SINGLE:
        accept = om.MimeTypes.single_image
    elif accept is OciManifestChoice.MULTIARCH:
        accept = om.MimeTypes.multiarch
    elif accept is OciManifestChoice.PREFER_MULTIARCH:
        accept = om.MimeTypes.prefer_multiarch
    else:
        raise NotImplementedError(accept)

    if pretty:
        manifest = oci_client.manifest(
            image_reference=image_reference,
            accept=accept,
        )
        manifest_raw = oci_client.manifest_raw(
            image_reference=image_reference,
            accept=accept,
        )

        pprint.pprint(dataclasses.asdict(manifest))

        if isinstance(manifest, om.OciImageManifest):
            total_size = sum(blob.size for blob in manifest.blobs())
            manifest_digest = hashlib.sha256(manifest_raw.content).hexdigest()

            print()
            print(f'{total_size=} {manifest_digest=}')
    else:
        manifest = oci_client.manifest_raw(
            image_reference=image_reference,
            accept=accept,
        )
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
    for chunk in blob.iter_content(chunk_size=4096):
        write(chunk)

    outfh.flush()
