import argparse
import json
import os.path
import platform
import pprint
import sys
import textwrap

import yaml

import oci.auth
import oci.client
import oci.model


def _oci_client(parsed):
    docker_cfg = parsed.docker_cfg
    if docker_cfg and not os.path.exists(docker_cfg):
        print(f'Error: not an existing file: {docker_cfg=}')
        exit(1)

    if not docker_cfg:
        for candidate in (
            os.path.expandvars('$HOME/.docker/config.json'),
            '/docker-cfg.json',
        ):
            if os.path.exists(candidate):
                docker_cfg = candidate
                break # first existing candidate wins

    # we already checked that _if_ user passed-in docker_cfg, it also exists; if user did _not_
    # pass-in docker-cfg, try anonymous authentication
    return oci.client.Client(
        credentials_lookup=oci.auth.docker_credentials_lookup(
            docker_cfg=docker_cfg,
            absent_ok=True,
        ),
    )


def _to_accept(parsed):
    if (accept := parsed.accept) == 'prefer-multiarch':
        accept = oci.model.MimeTypes.prefer_multiarch
    elif accept == 'single':
        accept = oci.model.MimeTypes.single_image
    elif accept == 'multiarch':
        accept = oci.model.MimeTypes.multiarch
    else:
        raise ValueError(accept) # this is a bug

    return accept


def manifest(parsed, oci_client: oci.client.Client):
    image_reference, = parsed.image_reference
    image_reference = oci.model.OciImageReference(image_reference)
    accept = _to_accept(parsed)

    manifest = None
    if parsed.platform:
        # todo: deduplicate w/ ctt/oci_platform.py
        if parsed.platform == 'local':
            osname = sys.platform
            if osname == 'win32':
                osname = 'windows'
            arch = platform.machine()
            if arch == 'x86_64':
                arch = 'amd64'
        else:
            try:
                osname, arch = parsed.platform.split('/')
            except ValueError:
                print(f'Error: expected --platform in form OS/ARCH, got {parsed.platform=}')
                exit(1)

        manifest = oci_client.manifest(
            image_reference=image_reference,
            accept=accept,
        )
        if isinstance(manifest, oci.model.OciImageManifestList):
            candidates = []
            for entry in manifest.manifests:
                if entry.platform.os != osname:
                    continue
                if entry.platform.architecture != arch:
                    continue
                candidates.append(entry)
            if len(candidates) == 0:
                print( 'Error: could not find any matching submanifest for:') # noqa
                print(f'       {image_reference=}')
                print(f'using  {parsed.platform=}')
                exit(1)
            elif len(candidates) > 1:
                print( 'Error: could not find any matching submanifest for:') # noqa
                print(f'       {image_reference=}')
                print(f'using  {parsed.platform=}')
                exit(1)
            manifest, = candidates
            image_reference = f'{image_reference.ref_without_tag}@{manifest.digest}'

    if parsed.format != 'pretty':
        manifest = oci_client.manifest_raw(
            image_reference=image_reference,
            accept=accept,
        )
        if parsed.format == 'raw':
            sys.stdout.buffer.write(manifest.content)
            if sys.stdout.isatty():
                sys.stdout.buffer.write('\n'.encode('utf-8'))
            sys.stdout.buffer.flush()
            exit(0)
        if parsed.format == 'yaml':
            print(yaml.safe_dump(manifest.json()))
            exit(0)
        if parsed.format == 'json-pretty':
            print(json.dumps(
                obj=manifest.json(),
                indent=2,
            ))
            exit(0)
    elif parsed.format == 'pretty':
        manifest = oci_client.manifest(
            image_reference=image_reference,
            accept=accept,
        )
        pprint.pprint(manifest)
    else:
        raise ValueError(parsed.format) # this is a bug


def blob(parsed, oci_client: oci.client.Client):
    image_reference, = parsed.image_reference
    image_reference = oci.model.OciImageReference(image_reference)
    accept = _to_accept(parsed)

    digest = parsed.digest
    index = parsed.index
    if not ((digest is None) ^ (index is None)):
        print('Error: exactly one of --digest, --index must be passed')
        exit(1)

    if index is not None:
        manifest = oci_client.manifest(
            image_reference=image_reference,
            accept=accept,
        )
        if not isinstance(manifest, oci.model.OciImageManifest):
            print(f'Error: {manifest.mediaType=}')
            print('Expected a single OCI-Image-Manifest')
            print('Hint: inspect manifest using `manifest` subcommand:')
            print(f'  oci manifest {image_reference}')
            exit(1)
        try:
            digest = manifest.layers[index].digest
        except IndexError:
            print(f'Error: no layer with {index=}')
            exit(1)

    if parsed.outfile == '-':
        if sys.stdout.isatty():
            print('refusing to write to interactive terminal (redirect stdout, or pass --outfile)')
            exit(1)

        outfh = sys.stdout.buffer
    else:
        outfh = open(parsed.outfile, 'wb')

    blob = oci_client.blob(
        image_reference=image_reference,
        digest=digest,
        stream=True,
    )

    for chunk in blob.iter_content(chunk_size=4096):
        outfh.write(chunk)
    outfh.flush()


def main():
    parser = argparse.ArgumentParser()
    subcmd_parsers = parser.add_subparsers(
        title='commands',
        required=True,
    )

    parser.add_argument('--docker-cfg', default=None)

    manifest_parser = subcmd_parsers.add_parser(
        'manifest',
        aliases=('m',),
        help='retrieve OCI-Image-Manifests',
    )
    manifest_parser.set_defaults(callable=manifest)
    manifest_parser.add_argument(
        'image_reference',
        nargs=1,
    )
    manifest_parser.add_argument(
        '--format',
        required=False,
        default='raw',
        choices=('raw', 'json-pretty', 'yaml', 'pretty'),
    )
    manifest_parser.add_argument(
        '--accept',
        required=False,
        default='prefer-multiarch',
        choices=('prefer-multiarch', 'single', 'multiarch',),
    )
    manifest_parser.add_argument(
        '--platform',
        required=False,
        default=None,
        help=textwrap.dedent(
            '''\
            expected format: OS/ARCH (e.g. linux/amd64) or `local`. If specified, and manifest
            is an OCI-Image-Index or ManifestList, will look for matching submanifest and
            print it instead of "toplevel"-manifest.
            '''),
    )

    blob_parser = subcmd_parsers.add_parser(
        'blob',
        aliases=('b',),
        help='retrieve (layer-)blobs from OCI-Artefacts',
    )
    blob_parser.set_defaults(callable=blob)
    blob_parser.add_argument(
        'image_reference',
        nargs=1,
    )
    blob_parser.add_argument(
        '--accept',
        required=False,
        default='prefer-multiarch',
        choices=('prefer-multiarch', 'single', 'multiarch',),
    )
    blob_parser.add_argument(
        '--digest',
        required=False,
        default=None,
        help='specify the layer to retrieve by digest',
    )
    blob_parser.add_argument(
        '--index',
        type=int,
        required=False,
        default=None,
        help='specify the layer to retrieve by index',
    )
    blob_parser.add_argument(
        '--outfile', '-o',
        required=False,
        default='-',
        help='where to write blob to (defaults to writing to stdout)',
    )

    parsed = parser.parse_args()

    oci_client = _oci_client(parsed=parsed)

    parsed.callable(
        parsed=parsed,
        oci_client=oci_client,
    )


if __name__ == '__main__':
    main()
