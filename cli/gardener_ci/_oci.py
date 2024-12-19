import concurrent.futures
import enum
import hashlib
import json
import pprint
import subprocess
import sys
import tabulate
import tarfile
import tempfile

import requests

import ccc.delivery
import ccc.oci
import ctx
import delivery.client
import delivery.util
import oci
import oci.model as om
import oci.workarounds as ow
import tarutil
import unixutil.scan as us
import version

__cmd_name__ = 'oci'


class OciManifestChoice(enum.Enum):
    PREFER_MULTIARCH = 'prefer-multiarch'
    SINGLE = 'single'
    MULTIARCH = 'multiarch'


def cp(
    src:str,
    tgt:str,
    annotations:[str]=list(),
):
    if annotations:
        annotations_dict = {}
        for a in annotations:
            k, v = a.split('=')
            annotations_dict[k] = v
    else:
        annotations_dict = None

    oci_client = ccc.oci.oci_client()

    oci.replicate_artifact(
        src_image_reference=src,
        tgt_image_reference=tgt,
        oci_client=oci_client,
        mode=oci.ReplicationMode.PREFER_MULTIARCH,
        annotations=annotations_dict,
    )


def tags(image: str):
    oci_client = ccc.oci.oci_client()

    print('\n'.join(oci_client.tags(image_reference=image)))


def _manifest(
    image_reference: str,
    oci_client,
):
    image_reference: om.OciImageReference = om.OciImageReference.to_image_ref(image_reference)
    manifest = oci_client.manifest(
        image_reference=image_reference,
        accept=om.MimeTypes.prefer_multiarch,
    )

    if isinstance(manifest, om.OciImageManifestList):
        manifest: om.OciImageManifestList
        manifest: om.OciImageManifestListEntry = manifest.manifests[0]
        sub_img_ref = f'{image_reference.ref_without_tag}@{manifest.digest}'
        manifest = oci_client.manifest(sub_img_ref)

    return manifest


def ls(
    image: str,
    long: bool=False, # inspired by ls --long ; aka: ls -l
):
    oci_client = ccc.oci.oci_client()
    image_reference: om.OciImageReference = om.OciImageReference.to_image_ref(image)

    manifest = _manifest(
        image_reference=image_reference,
        oci_client=oci_client,
    )

    for layer in manifest.layers:
        blob = oci_client.blob(image_reference=image_reference, digest=layer.digest)

        with tarfile.open(
            fileobj=tarutil.FilelikeProxy(generator=blob.iter_content(chunk_size=4096)),
            mode='r|*',
        ) as tf:
            for info in tf:
                if long:
                    suffix = ''
                    if info.isdir():
                        prefix = 'd'
                    elif info.isfile():
                        prefix = 'f'
                        suffix = f'({info.size})'
                    elif info.issym():
                        suffix = f'-> {info.linkname}'
                        prefix = 's'
                    elif info.islnk():
                        prefix = 'l'
                    else:
                        prefix = ' '

                    print(f'{prefix} {info.name} {suffix}')
                else:
                    print(info.name)


def cat(
    image: str,
    path: str,
    outfile: str='-',
):
    if outfile == '-' and sys.stdout.isatty():
        print('error: either redirect output, or specify --outfile')
        exit(1)

    image_reference = oci.model.OciImageReference.to_image_ref(image)

    oci_client = ccc.oci.oci_client()
    manifest = _manifest(
        image_reference=image_reference,
        oci_client=oci_client,
    )

    for layer in manifest.layers:
        blob = oci_client.blob(image_reference=image_reference, digest=layer.digest)

        with tarfile.open(
            fileobj=tarutil.FilelikeProxy(generator=blob.iter_content(chunk_size=4096)),
            mode='r|*',
        ) as tf:
            for info in tf:
                if path.removeprefix('/') != info.name.removeprefix('/'):
                    continue
                break
            else:
                continue

            if not info.isfile():
                print(f'error: {path=} is not a regular file')
                exit(1)
            if outfile == '-':
                outfile = sys.stdout.buffer
            else:
                outfile = open(outfile, 'wb')

            octects_left = info.size
            while octects_left:
                read = min(octects_left, 4096)
                outfile.write(tf.fileobj.read(read))
                octects_left -= read

            # stop after first match
            exit(0)


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


def _to_accept_mimetype(
    accept:OciManifestChoice=OciManifestChoice.PREFER_MULTIARCH,
):
    if accept is OciManifestChoice.SINGLE:
        accept = om.MimeTypes.single_image
    elif accept is OciManifestChoice.MULTIARCH:
        accept = om.MimeTypes.multiarch
    elif accept is OciManifestChoice.PREFER_MULTIARCH:
        accept = om.MimeTypes.prefer_multiarch
    else:
        raise NotImplementedError(accept)

    return accept


def manifest(
    image_reference: str,
    pretty:bool=True,
    accept:OciManifestChoice=OciManifestChoice.PREFER_MULTIARCH,
    print_expr=None,
):
    oci_client = ccc.oci.oci_client()

    accept = _to_accept_mimetype(accept)

    manifest_raw = oci_client.manifest_raw(
        image_reference=image_reference,
        accept=accept,
    )

    if pretty and not print_expr:
        manifest = oci.model.as_manifest(
            manifest=manifest_raw.text,
        )

        if isinstance(manifest, om.OciImageManifest):
            pprint.pprint(manifest.as_dict())
        else:
            pprint.pprint(manifest_raw.json())

        if isinstance(manifest, om.OciImageManifest):
            total_size = sum(blob.size for blob in manifest.blobs())
            manifest_raw_bytes = manifest_raw.content
            manifest_size = len(manifest_raw_bytes)
            manifest_digest = hashlib.sha256(manifest_raw_bytes).hexdigest()

            print()
            print(f'{total_size=} {manifest_digest=} {manifest_size=}')
        elif isinstance(manifest, om.OciImageManifestList):
            manifest_digest = hashlib.sha256(manifest_raw.content).hexdigest()
            print()
            print(f'{manifest_digest=}')

    elif not pretty and not print_expr:
        print(manifest_raw.text)
    else:
        manifest_bytes = manifest_raw.content
        manifest = oci.model.as_manifest(
            manifest=manifest_bytes,
        )

        # expose to eval
        size = len(manifest_bytes) # noqa
        digest = f'sha256:{hashlib.sha256(manifest_bytes).hexdigest()}' # noqa
        image_reference = oci.model.OciImageReference(image_reference) # noqa
        repository = image_reference.ref_without_tag # noqa

        print(eval(print_expr)) # nosec B307


def edit(
    image_reference: str,
    accept:OciManifestChoice=OciManifestChoice.PREFER_MULTIARCH,
    editor: str='vim',
):
    image_reference = om.OciImageReference(image_reference)
    oci_client = ccc.oci.oci_client()

    manifest_dict = oci_client.manifest_raw(
        image_reference=image_reference,
        accept=_to_accept_mimetype(accept),
    ).json()

    manifest_pretty = json.dumps(
        manifest_dict,
        indent=2,
    ).encode('utf-8')

    tmp = tempfile.NamedTemporaryFile()
    tmp.write(manifest_pretty)
    tmp.flush()

    # keep digest to find modifications
    digest = hashlib.sha256(manifest_pretty).hexdigest()

    res = subprocess.run((editor, tmp.name))
    if res.returncode != 0:
        print(f'editor returned {res.returncode=} - discarding changes!')
        exit(1)

    tmp.seek(0)
    file_digest = hashlib.sha256(tmp.read()).hexdigest()
    tmp.seek(0)

    if digest == file_digest:
        print('there were no changes - OCI Image Manifest will be left unchanged')
        exit(0)

    # upload altered manifest
    if image_reference.has_digest_tag:
        # if passed-in image-ref contained digest, target-ref must be patched
        image_reference = f'{image_reference.ref_without_tag}@sha256:{file_digest}'

    print(f'patching {image_reference}')
    oci_client.put_manifest(
        image_reference=image_reference,
        manifest=tmp.read(),
    )


def cfg(image_reference: str):
    oci_client = ccc.oci.oci_client()

    manifest = oci_client.manifest(image_reference=image_reference)
    cfg_blob = oci_client.blob(
        image_reference=image_reference,
        digest=manifest.config.digest,
        stream=False,
    ).content

    cfg_len = len(cfg_blob)
    cfg_dig = f'sha256:{hashlib.sha256(cfg_blob).hexdigest()}'

    pprint.pprint(json.loads(cfg_blob))

    print(f'{cfg_len} octetts')
    print(f'digest: {cfg_dig}')


def blob(
    image_reference: str,
    accept:OciManifestChoice=OciManifestChoice.SINGLE,
    digest: str=None,
    index:int=None,
    outfile: str='-',
):
    if not ((digest is not None) ^ (index is not None)):
        print('usage: exactly one of --digest, --index must be passed')
        exit(1)
    oci_client = ccc.oci.oci_client()

    if index is not None:
        manifest = oci_client.manifest(
            image_reference=image_reference,
            accept=_to_accept_mimetype(accept),
        )
        if not manifest.layers:
            print(f'{manifest.mediaType=}')
            print('image-manifest has no layers (hint: did you refer to a manifest-list?)')
            exit(1)
        try:
            digest = manifest.layers[index].digest
        except IndexError:
            print(f'no layer with {index=} present')
            exit(1)

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


def osinfo(
    image_reference: str,
    delivery_cfg_name: str=None,
):
    oci_client = ccc.oci.oci_client()
    if not delivery_cfg_name:
        delivery_cfg_name = ctx.cfg.ctx.delivery_cfg_name
    if delivery_cfg_name:
        cfg_factory = ctx.cfg_factory()
        delivery_cfg = cfg_factory.delivery_endpoints(delivery_cfg_name)

        delivery_client = delivery.client.DeliveryServiceClient(
            routes=delivery.client.DeliveryServiceRoutes(
                base_url=delivery_cfg.base_url(),
            ),
            auth_token_lookup=ccc.delivery.auth_token_lookup,
        )
    else:
        delivery_client = None

    manifest = oci_client.manifest(
        image_reference=image_reference,
        accept=om.MimeTypes.prefer_multiarch,
    )
    if isinstance(manifest, om.OciImageManifestList):
        img_ref = om.OciImageReference(image_reference)
        sub_img_ref = f'{img_ref.ref_without_tag}@{manifest.manifests[0].digest}'

        manifest = oci_client.manifest(sub_img_ref)

    last_os_info = None

    for layer in manifest.layers:
        layer_blob = oci_client.blob(
            image_reference=image_reference,
            digest=layer.digest,
        )
        fileproxy = tarutil.FilelikeProxy(
            layer_blob.iter_content(chunk_size=tarfile.BLOCKSIZE)
        )
        tf = tarfile.open(fileobj=fileproxy, mode='r|*')
        if (os_info := us.determine_osinfo(tf)):
            last_os_info = os_info

    os_info = last_os_info
    pprint.pprint(os_info)

    if not delivery_client:
        print('no delivery-cfg found (use --delivery-cfg-name to configure)')
        print('will exit now')
        exit(0)

    os_infos = delivery_client.os_release_infos(
        os_id=os_info.ID,
        absent_ok=True,
    )

    if not os_infos:
        print(f'did not find os-infos for {os_info.ID=}')
        exit(0)

    branch_info = delivery.util.find_branch_info(
        os_id=os_info,
        os_infos=os_infos,
    )

    if not branch_info:
        print(f'did not find branch-info for {os_info.ID=} {os_info.VERSION=}')
        exit(1)

    print()
    print('Branch-Info:')
    print()
    pprint.pprint(branch_info)

    eol = delivery.util.branch_reached_eol(
        os_id=os_info,
        os_infos=os_infos,
    )

    have_update = delivery.util.update_available(
        os_id=os_info,
        os_infos=os_infos,
        ignore_if_patchlevel_is_next_to_greatest=False,
    )

    almost_up_to_date = not delivery.util.update_available(
        os_id=os_info,
        os_infos=os_infos,
        ignore_if_patchlevel_is_next_to_greatest=True,
    )

    distroless = os_info.is_distroless

    print(
        tabulate.tabulate(
            headers=('info', 'value'),
            tabular_data=(
                ('eol', eol,),
                ('update-available', have_update,),
                ('(almost)-up-to-date', almost_up_to_date,),
                ('distroless', distroless,),
            ),
        )
    )

    if have_update and almost_up_to_date:
        print()
        print('almost-up-to-date: not more than one patchlevel behind')
