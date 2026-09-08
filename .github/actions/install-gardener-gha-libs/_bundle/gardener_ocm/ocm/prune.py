import collections
import collections.abc
import concurrent.futures
import dataclasses
import enum
import hashlib
import logging
import threading

import oci.client as oc
import oci.model as om
import oci.nonstd
import oci.util
import ocm
import ocm.iter as ocm_iter
import ocm.retrieve
import version as version_mod

logger = logging.getLogger(__name__)


class EnumerationMode(enum.IntFlag):
    KNOWN_REPOSITORIES = 1  # unset → full-registry enumeration via vendor API
    RESTRICT_TO_OCM_REPO = 2  # unset → include external OCI refs in retain set


_DEFAULT_MODE = EnumerationMode.KNOWN_REPOSITORIES | EnumerationMode.RESTRICT_TO_OCM_REPO


def _make_lookup(ocm_repo, oci_client):
    return ocm.retrieve.create_default_component_descriptor_lookup(
        ocm_repository_lookup=ocm.retrieve.ocm_repository_lookup(ocm_repo.oci_ref),
        oci_client=oci_client,
    )


@dataclasses.dataclass
class PruneCtx:
    ocm_repo: ocm.OciOcmRepository
    oci_client: oc.Client
    mode: EnumerationMode = _DEFAULT_MODE
    retain_set: set = dataclasses.field(default_factory=set)
    candidates: set = dataclasses.field(default_factory=set)
    lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def parse_component_ref(component_ref: str) -> tuple[str, str | None]:
    if ':' in component_ref:
        name, ver = component_ref.rsplit(':', 1)
        return name, ver
    return component_ref, None


def resolve_versions(
    component_name: str,
    anchor_version: str | None,
    ocm_repo: ocm.OciOcmRepository,
    oci_client: oc.Client,
    keep_versions: int,
) -> list[str]:
    '''Return up to keep_versions version strings <= anchor_version, ascending semver.
    If anchor_version is None, the greatest available version is used.'''
    all_versions = oci_client.tags(image_reference=ocm_repo.component_oci_ref(component_name))

    if not all_versions:
        logger.warning(f'{component_name!r}: no versions found in {ocm_repo.oci_ref!r}')
        return []

    try:
        sorted_versions = sorted(all_versions, key=version_mod.parse_to_semver)
    except Exception:
        logger.warning(f'{component_name!r}: semver sort failed; falling back to lexicographic')
        sorted_versions = sorted(all_versions)

    if anchor_version is None:
        anchor_version = sorted_versions[-1]
        logger.info(f'{component_name!r}: anchor resolved to {anchor_version!r}')

    try:
        anchor_sv = version_mod.parse_to_semver(anchor_version)
        eligible = [v for v in sorted_versions if version_mod.parse_to_semver(v) <= anchor_sv]
    except Exception:
        eligible = [v for v in sorted_versions if v <= anchor_version]

    return eligible[-keep_versions:]


def _iter_resource_refs(
    node: ocm_iter.ResourceNode,
    ocm_repo: ocm.OciOcmRepository,
) -> collections.abc.Iterator[str]:
    access = node.resource.access
    if isinstance(access, ocm.OciAccess):
        yield access.imageReference
    elif isinstance(access, ocm.RelativeOciAccess):
        base = ocm_repo.oci_ref.rstrip('/')
        yield f'{base}/{access.reference.lstrip("/")}'


def _in_scope_of_ocm_repo(ref: str, ocm_repo: ocm.OciOcmRepository) -> bool:
    base = oci.util.normalise_image_reference(ocm_repo.oci_ref).rstrip('/')
    return oci.util.normalise_image_reference(ref).startswith(base + '/')


def _account_prefix(ocm_repo: ocm.OciOcmRepository) -> str:
    ref = om.OciImageReference(ocm_repo.oci_ref)
    parts = ref.name.split('/')
    account = parts[0] if parts else ''
    return f'{ref.netloc}/{account}' if account else ref.netloc


# ---------------------------------------------------------------------------
# phase I-A: retain set
# ---------------------------------------------------------------------------

def prune(component: ocm.ComponentIdentity, ctx: PruneCtx, lookup=None):
    '''Recursively add all OCI refs reachable from component to ctx.retain_set.'''
    if lookup is None:
        lookup = _make_lookup(ctx.ocm_repo, ctx.oci_client)

    desc_ref = oci.util.normalise_image_reference(
        ctx.ocm_repo.component_version_oci_ref(component.name, component.version)
    )

    with ctx.lock:
        if desc_ref in ctx.retain_set:
            return
        ctx.retain_set.add(desc_ref)

    cd = lookup(component, absent_ok=True)
    if cd is None:
        logger.warning(f'could not resolve {component.name}:{component.version}')
        return

    for node in ocm_iter.iter(
        component=cd.component,
        lookup=lookup,
        recursion_depth=1,
    ):
        if isinstance(node, ocm_iter.ResourceNode) and len(node.path) == 1:
            for ref in _iter_resource_refs(node, ctx.ocm_repo):
                if ctx.mode & EnumerationMode.RESTRICT_TO_OCM_REPO:
                    if not _in_scope_of_ocm_repo(ref, ctx.ocm_repo):
                        continue
                with ctx.lock:
                    ctx.retain_set.add(oci.util.normalise_image_reference(ref))
        elif isinstance(node, ocm_iter.ComponentNode) and len(node.path) == 2:
            prune(
                ocm.ComponentIdentity(
                    name=node.component.name,
                    version=node.component.version,
                ),
                ctx,
                lookup,
            )


# ---------------------------------------------------------------------------
# phase I-B: candidate enumeration
# ---------------------------------------------------------------------------

def _list_tags(repo: str, oci_client: oc.Client) -> list[str]:
    try:
        return list(oci_client.iter_tags(repo))
    except Exception as e:
        logger.warning(f'{repo!r}: tag listing failed: {e}')
        return []


def _repos_known(ctx: PruneCtx) -> set:
    base = oci.util.normalise_image_reference(ctx.ocm_repo.oci_ref).rstrip('/')
    return {
        om.OciImageReference(r).ref_without_tag
        for r in ctx.retain_set
        if oci.util.normalise_image_reference(r).startswith(base + '/')
    }


def _repos_full_registry(ctx: PruneCtx) -> set:
    logger.warning(
        'full-registry enumeration is active — may discover artefacts not managed by '
        'ocm/ctt replicate'
    )
    prefix = _account_prefix(ctx.ocm_repo)
    repos = set()
    try:
        for repo_name in oci.nonstd.iter_repositories(
            client=ctx.oci_client,
            image_reference=prefix,
            raise_if_unsupported=False,
        ):
            repos.add(f'{prefix}/{repo_name}')
    except Exception as e:
        logger.warning(f'{prefix!r}: repository enumeration failed: {e}')
    return repos


def enumerate_candidates(ctx: PruneCtx, jobs: int = 8):
    '''Populate ctx.candidates with tagged refs from target repos not in retain_set.'''
    repos = (
        _repos_known(ctx)
        if ctx.mode & EnumerationMode.KNOWN_REPOSITORIES
        else _repos_full_registry(ctx)
    )

    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(_list_tags, repo, ctx.oci_client): repo for repo in repos}
        for future in concurrent.futures.as_completed(futures):
            for tag in future.result():
                repo = futures[future]
                ref = oci.util.normalise_image_reference(f'{repo}:{tag}')
                if ref not in ctx.retain_set:
                    with ctx.lock:
                        ctx.candidates.add(ref)


# ---------------------------------------------------------------------------
# phase II: deletion
# ---------------------------------------------------------------------------

def _resolve_digest(ref: str, oci_client: oc.Client) -> str | None:
    raw = oci_client.manifest_raw(image_reference=ref, absent_ok=True)
    if raw is None:
        return None
    return 'sha256:' + hashlib.sha256(raw.content).hexdigest()


def _group_by_digest(
    refs: collections.abc.Iterable[str],
    oci_client: oc.Client,
    jobs: int = 8,
) -> dict[str, list[str]]:
    '''Returns {digest_ref: [tag_refs]}, grouping refs that resolve to the same manifest.'''
    groups = collections.defaultdict(list)
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futs = {pool.submit(_resolve_digest, ref, oci_client): ref for ref in refs}
        for fut in concurrent.futures.as_completed(futs):
            ref = futs[fut]
            digest = fut.result()
            if digest is None:
                logger.warning(f'{ref!r}: manifest absent — skipping')
                continue
            digest_ref = f'{om.OciImageReference(ref).ref_without_tag}@{digest}'
            groups[digest_ref].append(ref)
    return dict(groups)


def _delete_group(digest_ref: str, tag_refs: list[str], oci_client: oc.Client):
    for tag_ref in tag_refs:
        try:
            oci_client.delete_manifest(image_reference=tag_ref, purge=False, absent_ok=True)
        except Exception as e:
            logger.warning(f'untag {tag_ref!r} failed: {e}')
    oci_client.delete_manifest(image_reference=digest_ref, purge=False, absent_ok=True)
    logger.info(f'removed {digest_ref} ({len(tag_refs)} tag(s))')


def _batch_delete_all_aws(groups: dict, oci_client: oc.Client):
    all_refs = []
    for digest_ref, tag_refs in groups.items():
        all_refs.extend(tag_refs)
        all_refs.append(digest_ref)

    if not all_refs:
        return

    oci_client.batch_delete_manifests(
        image_reference=all_refs[0],
        refs=all_refs,
    )
    logger.info(
        f'batch-deleted {len(groups)} manifest(s) '
        f'({sum(len(t) for t in groups.values())} tag(s) total)'
    )


def delete_all(
    refs: collections.abc.Iterable[str],
    oci_client: oc.Client,
    jobs: int = 8,
):
    refs = list(refs)
    if not refs:
        return

    groups = _group_by_digest(refs, oci_client, jobs=jobs)
    logger.info(
        f'pruning {len(groups)} unique manifest(s) '
        f'({sum(len(t) for t in groups.values())} tag(s) total)'
    )

    if om.OciRegistryType.from_image_ref(refs[0]) is om.OciRegistryType.AWS:
        _batch_delete_all_aws(groups, oci_client)
        return

    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futs = [
            pool.submit(_delete_group, digest_ref, tag_refs, oci_client)
            for digest_ref, tag_refs in groups.items()
        ]
        concurrent.futures.wait(futs)

    if failed := [f for f in futs if f.exception()]:
        raise RuntimeError(f'{len(failed)} deletion(s) failed')
