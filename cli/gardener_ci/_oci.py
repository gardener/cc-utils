import concurrent.futures
import enum
import hashlib
import pprint
import sys
import tarfile
import tempfile

import requests

import ccc.oci
import oci
import oci.model as om
import oci.workarounds as ow
import unixutil.scan as us
import version

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


def purge(image: str):
    oci_client = ccc.oci.oci_client()

    oci_client.delete_manifest(
        image_reference=image,
        purge=True,
    )
    print(f'purged {image}')


def purge_old(
    image: str,
    keep:int=128,
    skip_non_semver:bool=True,
):
    oci_client = ccc.oci.oci_client()
    pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=8,
    )
    tags = [
        t for t in oci_client.tags(image_reference=image)
        if not skip_non_semver or version.is_semver_parseable(t)
    ]

    def sloppy_semver_parse(v: str):
        if v.count('-') > 1:
            v = v.split('-', 1)[0] + '-suffix' # discard other suffixes

        return version.parse_to_semver(v)

    tags = sorted([
        tag for tag in tags
        if not tag.startswith('latest')
        ],
      key=sloppy_semver_parse,
    ) # smallest version comes first

    remove_count = len(tags) - keep

    if remove_count < 1:
        print(f'will not remove images - there were less than {keep}')
        return

    print(f'found {len(tags)} image(s) - will purge {remove_count}')

    def purge_image(image_ref: str):
        print(f'purging {image_ref}')
        manifest = oci_client.manifest(
            image_reference=image_ref,
            accept=om.MimeTypes.prefer_multiarch,
        )
        try:
            oci_client.delete_manifest(
                image_reference=image_ref,
                purge=True,
                accept=om.MimeTypes.prefer_multiarch,
            )
        except requests.HTTPError as http_error:
            error_dict = http_error.response.json()
            errors = error_dict['errors']

            for e in errors:
                if e['code'] == 'GOOGLE_MANIFEST_DANGLING_PARENT_IMAGE':
                    msg = e['message']
                    parent_image_digest = msg.rsplit(' ', 1)[-1]
                    parent_img_ref = om.OciImageReference(image_ref)
                    print(f'warning: will purge dangling {parent_image_digest=}')
                    oci_client.delete_manifest(
                        image_reference=f'{parent_img_ref.ref_without_tag}@{parent_image_digest}',
                    )
            raise http_error

        if isinstance(manifest, om.OciImageManifest):
            return
        elif not isinstance(manifest, om.OciImageManifestList):
            raise ValueError(manifest)

        # manifest-list (aka multi-arch)
        image_ref = om.OciImageReference(image_ref)

        def iter_platform_refs():
            repository = image_ref.ref_without_tag
            base_tag = image_ref.tag

            for submanifest in manifest.manifests:
                p = submanifest.platform
                yield f'{repository}:{base_tag}-{p.os}-{p.architecture}'

        for ref in iter_platform_refs():
            if not oci_client.head_manifest(
                image_reference=ref,
                absent_ok=True,
            ):
                continue

            oci_client.delete_manifest(
                image_reference=ref,
                purge=True,
            )

    def iter_image_refs_to_purge():
        for idx, tag in enumerate(tags, 1):
            if idx > remove_count:
                print(f'stopping the purge at {tag}')
                return

            yield f'{image}:{tag}'

    for _ in pool.map(purge_image, iter_image_refs_to_purge()):
        pass


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

        pprint.pprint(manifest.as_dict())

        if isinstance(manifest, om.OciImageManifest):
            total_size = sum(blob.size for blob in manifest.blobs())
            manifest_digest = hashlib.sha256(manifest_raw.content).hexdigest()

            print()
            print(f'{total_size=} {manifest_digest=}')
        elif isinstance(manifest, om.OciImageManifestList):
            manifest_digest = hashlib.sha256(manifest_raw.content).hexdigest()
            print()
            print(f'{manifest_digest=}')

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


def sanitise(image_reference: str):
    oci_client = ccc.oci.oci_client()

    manifest = oci_client.manifest(image_reference=image_reference)
    cfg_blob = oci_client.blob(
        image_reference=image_reference,
        digest=manifest.config.digest,
        stream=False,
    ).content

    if ow.is_cfg_blob_sane(manifest, cfg_blob=cfg_blob):
        print(f'{image_reference} was already sane - nothing to do')
        return

    patched_ref = ow.sanitise_image(image_ref=image_reference, oci_client=oci_client)

    print(patched_ref)


def osinfo(image_reference: str):
    oci_client = ccc.oci.oci_client()

    with tempfile.TemporaryFile() as tmpf:
        manifest = oci_client.manifest(image_reference=image_reference)
        first_layer_blob = oci_client.blob(
            image_reference=image_reference,
            digest=manifest.layers[0].digest,
        )
        for chunk in first_layer_blob.iter_content(chunk_size=4096):
            tmpf.write(chunk)

        tmpf.seek(0)
        tf = tarfile.open(fileobj=tmpf, mode='r')

        osi_info = us.determine_osinfo(tf)

    pprint.pprint(osi_info)
