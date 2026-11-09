import collections.abc
import hashlib
import os
import sys

# TODO(tmp-gh-tools): remove before merging to master — only needed on persistent runners
# where an old gardener-ocm PyPI package may shadow the local ocm module.
# ensure the local ocm module (from cc-utils checkout) takes precedence
# over the installed gardener-ocm package which may not have all symbols
_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)
for _k in list(sys.modules):
    if _k == 'ocm' or _k.startswith('ocm.'):
        del sys.modules[_k]
import magic
import yaml

import ocm


def iter_artefacts(
    artefacts_files: collections.abc.Iterable[str],
) -> collections.abc.Generator[dict, None, None]:
    '''
    iterate over dicts found in artefacts_files (interpreted
    as YAML-Documents). If toplevel element is a list, list-items are yielded.
    '''
    def iter_artefact(obj: list | dict):
        if isinstance(obj, list):
            yield from obj
        elif isinstance(obj, dict):
            yield obj
        else:
            raise RuntimeError(f'exepected either a list, or a dict, got: {obj=}')

    for artefacts_file in artefacts_files:
        with open(artefacts_file) as f:
            for obj in yaml.safe_load_all(f):
                yield from iter_artefact(obj)


def content_address_blobs(
    blobs_dir: str,
) -> dict[str, str]:
    '''
    Content-addresses all regular files in blobs_dir:
    - computes sha256 of each file
    - renames the file to `sha256:<hexdigest>` (if not already named that way)
    - creates a symlink at the original name pointing to the digest-named file

    Returns a mapping of original filenames (and blobs_dir-prefixed paths) to
    their digest names, for use in patching localBlob references.
    '''
    orig_to_digest = {}

    if not os.path.isdir(blobs_dir):
        return orig_to_digest

    for fname in os.listdir(blobs_dir):
        fpath = os.path.join(blobs_dir, fname)
        if not os.path.isfile(fpath):
            continue

        fhash = hashlib.sha256()
        with open(fpath, 'rb') as f:
            while chunk := f.read(8192):
                fhash.update(chunk)

        digest_name = f'sha256:{fhash.hexdigest()}'
        if fname != digest_name:
            os.replace(fpath, os.path.join(blobs_dir, digest_name))
            os.symlink(digest_name, fpath)
            orig_to_digest[fname] = digest_name
            orig_to_digest[os.path.join(blobs_dir, fname)] = digest_name

    return orig_to_digest


def patch_local_blob_refs(
    artefacts: list[dict],
    orig_to_digest: dict[str, str],
    blobs_dir: str,
) -> None:
    '''
    Patches localBlob access entries in artefacts by converting them to OCM v2
    input: {type: file, path: <digest>} format.

    For sha256:-prefixed refs: directly sets artefact['input'] and removes artefact['access'].
    For filename refs: resolves to digest name, sets artefact['input'] with path: digest_name,
    removes artefact['access'].
    Also fills in the `type` field via mime detection if missing.

    Exits with code 1 (via SystemExit) if a referenced blob file cannot be resolved.
    '''
    def find_local_blobfile(local_ref):
        candidate = os.path.join(blobs_dir, local_ref)
        if os.path.isfile(candidate):
            return candidate
        if os.path.isfile(local_ref):
            return local_ref

    for artefact in artefacts:
        if not (access := artefact.get('access')):
            continue
        if ocm.AccessType(access.get('type')) is not ocm.AccessType.LOCAL_BLOB:
            continue
        if not (local_ref := access.get('localReference')):
            continue

        if local_ref.startswith('sha256:'):
            artefact['input'] = {
                'type': str(ocm.InputType.FILE),
                'path': local_ref,
                'mediaType': access.get('mediaType'),
            }
            del artefact['access']
            continue
        if not find_local_blobfile(local_ref):
            raise ValueError(f'did not find blobfile for {artefact=}')
        digest_name = orig_to_digest.get(local_ref)
        if not digest_name:
            raise ValueError(f'did not find digest for local blobfile {artefact=}')

        artefact['input'] = {
            'type': str(ocm.InputType.FILE),
            'path': digest_name,
            'mediaType': access.get('mediaType'),
        }
        del artefact['access']

        print(
            f'INFO: patched {artefact["name"]}\'s localBlob access to input '
            f'{digest_name=} (was: {local_ref=})'
        )

        if 'type' not in artefact:
            artefact['type'] = magic.from_file(
                os.path.realpath(os.path.join(blobs_dir, local_ref)),
                mime=True,
            )
