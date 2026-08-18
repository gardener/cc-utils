'''
Prune stale OCI artefacts from target registries.

For a given set of root OCM component versions, all OCI artefacts reachable
from those roots are collected into a retain set; every other tagged artefact
in the same repositories (or, optionally, the entire registry) is a candidate
for removal.

Two enumeration modes are supported:

  known-repositories (default)
    Only repositories already referenced by the retain-set are inspected for
    extra tags.  Completely abandoned repositories are invisible to this mode.

  full-registry
    Uses non-standard vendor APIs (currently: Keppel) to enumerate *all*
    repositories in each registry.  Catches abandoned repos, but may purge
    artefacts not placed there by ocm/ctt replicate.  Opt-in only.
'''
import collections.abc
import concurrent.futures
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


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _parse_component_ref(component_ref: str) -> tuple[str, str | None]:
    if ':' in component_ref:
        name, ver = component_ref.rsplit(':', 1)
        return name, ver
    return component_ref, None


def _resolve_versions(
    component_name: str,
    anchor_version: str | None,
    ocm_repo: ocm.OciOcmRepository,
    oci_client: oc.Client,
    keep_versions: int,
) -> list[str]:
    '''
    Return up to `keep_versions` version strings <= `anchor_version`, sorted
    ascending by relaxed semver.  If `anchor_version` is None, the greatest
    available version is used as anchor.
    '''
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


def _make_lookup(
    ocm_repo: ocm.OciOcmRepository,
    oci_client: oc.Client,
):
    return ocm.retrieve.create_default_component_descriptor_lookup(
        ocm_repository_lookup=ocm.retrieve.ocm_repository_lookup(ocm_repo.oci_ref),
        oci_client=oci_client,
    )


# ---------------------------------------------------------------------------
# retain-set construction
# ---------------------------------------------------------------------------

def build_retain_set(
    component_refs: list[str],
    ocm_repo: ocm.OciOcmRepository,
    oci_client: oc.Client,
    keep_versions: int = 1,
    lookup=None,
) -> frozenset[str]:
    '''
    Build the complete set of OCI references to retain, normalised.

    Each entry in `component_refs` is ``NAME`` (greatest version) or
    ``NAME:VERSION``.  When `keep_versions` > 1, additional older versions
    are also retained (anchor + N-1 next-older).
    '''
    if lookup is None:
        lookup = _make_lookup(ocm_repo, oci_client)

    retain = set()
    visited = set()  # (name, version) — skips already-traversed subtrees

    def _visit(name: str, ver: str):
        key = (name, ver)
        if key in visited:
            return
        visited.add(key)

        cd = lookup(ocm.ComponentIdentity(name=name, version=ver), absent_ok=True)
        if cd is None:
            logger.warning(f'could not resolve {name}:{ver}')
            return

        retain.add(ocm_repo.component_version_oci_ref(name, ver))

        # ocm.iter handles both standard componentReferences and
        # ExtraComponentReferencesLabel transparently; recursion_depth=1 gives
        # only direct children, keeping the per-level visited guard effective.
        for node in ocm_iter.iter(
            component=cd.component,
            lookup=lookup,
            recursion_depth=1,
        ):
            if isinstance(node, ocm_iter.ResourceNode) and len(node.path) == 1:
                retain.update(_iter_resource_refs(node, ocm_repo))
            elif isinstance(node, ocm_iter.ComponentNode) and len(node.path) == 2:
                _visit(node.component.name, node.component.version)

    for component_ref in component_refs:
        name, anchor = _parse_component_ref(component_ref)
        for ver in _resolve_versions(name, anchor, ocm_repo, oci_client, keep_versions):
            _visit(name, ver)

    return frozenset(oci.util.normalise_image_reference(r) for r in retain)


# ---------------------------------------------------------------------------
# candidate discovery
# ---------------------------------------------------------------------------

def _list_tags(repo: str, oci_client: oc.Client) -> list[str]:
    try:
        return list(oci_client.iter_tags(repo))
    except Exception as e:
        logger.warning(f'{repo!r}: tag listing failed: {e}')
        return []


def _discover_candidates(
    repos: collections.abc.Iterable[str],
    retain_set: frozenset[str],
    oci_client: oc.Client,
    jobs: int,
) -> collections.abc.Iterator[str]:
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(_list_tags, repo, oci_client): repo for repo in repos}
        for future in concurrent.futures.as_completed(futures):
            repo = futures[future]
            for tag in future.result():
                ref = f'{repo}:{tag}'
                if oci.util.normalise_image_reference(ref) not in retain_set:
                    yield ref


def iter_candidates_known_repositories(
    retain_set: frozenset[str],
    oci_client: oc.Client,
    ocm_repo: ocm.OciOcmRepository,
    jobs: int = 8,
) -> list[str]:
    base = oci.util.normalise_image_reference(ocm_repo.oci_ref).rstrip('/')
    repos = {
        om.OciImageReference(r).ref_without_tag
        for r in retain_set
        if oci.util.normalise_image_reference(r).startswith(base + '/')
    }
    return list(_discover_candidates(repos, retain_set, oci_client, jobs))


def iter_candidates_full_registry(
    retain_set: frozenset[str],
    oci_client: oc.Client,
    ocm_repo: ocm.OciOcmRepository,
    jobs: int = 8,
) -> list[str]:
    logger.warning(
        'full-registry enumeration is active — may discover artefacts not managed by '
        'ocm/ctt replicate'
    )

    ref = om.OciImageReference(ocm_repo.oci_ref)
    parts = ref.name.split('/')
    account = parts[0] if parts else ''
    prefix = f'{ref.netloc}/{account}' if account else ref.netloc

    all_repos = set()
    try:
        for repo_name in oci.nonstd.iter_repositories(
            client=oci_client,
            image_reference=prefix,
            raise_if_unsupported=False,
        ):
            all_repos.add(f'{ref.netloc}/{repo_name}')
    except Exception as e:
        logger.warning(f'{prefix!r}: repository enumeration failed: {e}')

    return list(_discover_candidates(all_repos, retain_set, oci_client, jobs))


# ---------------------------------------------------------------------------
# deletion — concurrent bottom-up tree traversal
# ---------------------------------------------------------------------------

def _delete_one(ref: str, oci_client: oc.Client):
    try:
        oci_client.delete_manifest(
            image_reference=ref,
            purge=True,
            absent_ok=True,
        )
        logger.info(f'removed {ref}')
    except Exception as e:
        logger.warning(f'failed to remove {ref!r}: {e}')


def _delete_component_subtree(
    component_name: str,
    component_version: str,
    ocm_repo: ocm.OciOcmRepository,
    lookup,
    oci_client: oc.Client,
    retain_set: frozenset[str],
    deleted: set,
    lock: threading.Lock,
    executor: concurrent.futures.ThreadPoolExecutor,
) -> threading.Event | None:
    '''
    Recursively delete a component subtree, resources-first, bottom-up.

    Spawns a coordination thread that:
      1. Recurses concurrently into direct sub-components (via ocm.iter,
         which handles ExtraComponentReferencesLabel transparently).
      2. Waits for all sub-components to finish.
      3. Submits own OCI resource deletions to `executor` and waits.
      4. Deletes own component-descriptor OCI artefact.
      5. Sets the returned Event.

    Returns None if this component is already being processed (dedup guard).
    '''
    desc_ref = oci.util.normalise_image_reference(
        ocm_repo.component_version_oci_ref(component_name, component_version),
    )
    done = threading.Event()

    with lock:
        if desc_ref in deleted:
            return None
        deleted.add(desc_ref)

    cd = lookup(
        ocm.ComponentIdentity(name=component_name, version=component_version),
        absent_ok=True,
    )

    def _coordinate():
        child_events = []

        if cd is not None:
            for node in ocm_iter.iter(
                component=cd.component,
                lookup=lookup,
                recursion_depth=1,
            ):
                if not (isinstance(node, ocm_iter.ComponentNode) and len(node.path) == 2):
                    continue
                child_desc_ref = oci.util.normalise_image_reference(
                    ocm_repo.component_version_oci_ref(
                        node.component.name,
                        node.component.version,
                    )
                )
                if child_desc_ref in retain_set:
                    continue
                ev = _delete_component_subtree(
                    component_name=node.component.name,
                    component_version=node.component.version,
                    ocm_repo=ocm_repo,
                    lookup=lookup,
                    oci_client=oci_client,
                    retain_set=retain_set,
                    deleted=deleted,
                    lock=lock,
                    executor=executor,
                )
                if ev is not None:
                    child_events.append(ev)

        for ev in child_events:
            ev.wait()

        resource_futs = []
        if cd is not None:
            for node in ocm_iter.iter(
                component=cd.component,
                lookup=lookup,
                recursion_depth=0,
            ):
                if not isinstance(node, ocm_iter.ResourceNode):
                    continue
                for ref in _iter_resource_refs(node, ocm_repo):
                    norm_ref = oci.util.normalise_image_reference(ref)
                    if norm_ref in retain_set:
                        continue
                    with lock:
                        if norm_ref in deleted:
                            continue
                        deleted.add(norm_ref)
                    resource_futs.append(executor.submit(_delete_one, ref, oci_client))

        concurrent.futures.wait(resource_futs)
        desc_oci_ref = ocm_repo.component_version_oci_ref(component_name, component_version)
        _delete_one(desc_oci_ref, oci_client)
        done.set()

    threading.Thread(target=_coordinate, daemon=True).start()
    return done


def delete_candidates(
    candidates: list[str],
    ocm_repo: ocm.OciOcmRepository,
    lookup,
    oci_client: oc.Client,
    retain_set: frozenset[str],
    jobs: int = 8,
):
    '''
    Delete all candidates.

    Component-descriptor refs drive concurrent bottom-up tree traversal:
    sub-components are fully purged before their parent's descriptor is removed.
    Remaining resource refs not reached by tree traversal are deleted directly.
    '''
    deleted = set()
    lock = threading.Lock()
    completion_events = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as executor:
        for ref in candidates:
            norm_ref = oci.util.normalise_image_reference(ref)
            if 'component-descriptors/' not in norm_ref:
                continue
            prefix = f'{ocm_repo.oci_ref.rstrip("/")}/component-descriptors/'
            if not norm_ref.startswith(prefix):
                continue
            rest = norm_ref[len(prefix):]
            if ':' not in rest:
                continue
            name, version = rest.rsplit(':', 1)

            ev = _delete_component_subtree(
                component_name=name,
                component_version=version,
                ocm_repo=ocm_repo,
                lookup=lookup,
                oci_client=oci_client,
                retain_set=retain_set,
                deleted=deleted,
                lock=lock,
                executor=executor,
            )
            if ev is not None:
                completion_events.append(ev)

        for ev in completion_events:
            ev.wait()

        # delete any remaining resource refs not reached via component tree traversal
        orphan_futs = []
        for ref in candidates:
            norm_ref = oci.util.normalise_image_reference(ref)
            if norm_ref in retain_set:
                continue
            with lock:
                if norm_ref in deleted:
                    continue
                deleted.add(norm_ref)
            orphan_futs.append(executor.submit(_delete_one, ref, oci_client))

        concurrent.futures.wait(orphan_futs)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def prune(
    component_refs: list[str],
    ocm_repo: ocm.OciOcmRepository,
    oci_client: oc.Client,
    keep_versions: int = 1,
    enumeration_mode: str = 'known-repositories',
    dry_run: bool = False,
    jobs: int = 8,
    retained_outfile: str | None = None,
    candidates_outfile: str | None = None,
):
    lookup = _make_lookup(ocm_repo, oci_client)

    print(f'Building retain set from {len(component_refs)} root component(s)...')
    retain_set = build_retain_set(
        component_refs=component_refs,
        ocm_repo=ocm_repo,
        oci_client=oci_client,
        keep_versions=keep_versions,
        lookup=lookup,
    )
    print(f'Retain set: {len(retain_set)} OCI reference(s)')

    if retained_outfile:
        with open(retained_outfile, 'w') as fh:
            for ref in sorted(retain_set):
                fh.write(ref + '\n')
        print(f'Retain set written to {retained_outfile!r}')

    if enumeration_mode == 'full-registry':
        candidates = iter_candidates_full_registry(
            retain_set=retain_set,
            oci_client=oci_client,
            ocm_repo=ocm_repo,
            jobs=jobs,
        )
    else:
        candidates = iter_candidates_known_repositories(
            retain_set=retain_set,
            oci_client=oci_client,
            ocm_repo=ocm_repo,
            jobs=jobs,
        )

    print(f'Found {len(candidates)} candidate(s) to prune')

    if candidates_outfile:
        with open(candidates_outfile, 'w') as fh:
            for ref in sorted(candidates):
                fh.write(ref + '\n')
        print(f'Candidate list written to {candidates_outfile!r}')

    if dry_run:
        print('Dry run — nothing removed')
        for ref in sorted(candidates):
            print(f'  would remove: {ref}')
        return

    delete_candidates(
        candidates=candidates,
        ocm_repo=ocm_repo,
        lookup=lookup,
        oci_client=oci_client,
        retain_set=retain_set,
        jobs=jobs,
    )

    print(f'Pruned {len(candidates)} artefact(s)')
