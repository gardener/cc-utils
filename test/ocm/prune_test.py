import types

import ocm
import ocm.prune as prune


TARGET_BASE = 'keppel.example.com/my-account'
SOURCE_BASE = 'europe-docker.pkg.dev/other-project/releases-public'

OCM_REPO = ocm.OciOcmRepository(baseUrl=TARGET_BASE)


def _ctx_with_retain(*refs):
    ctx = prune.PruneCtx(ocm_repo=OCM_REPO, oci_client=None)
    ctx.retain_set = set(refs)
    return ctx


# ---------------------------------------------------------------------------
# known-repositories candidate repos
# ---------------------------------------------------------------------------

def test_known_repos_includes_target_repos():
    ctx = _ctx_with_retain(
        f'{TARGET_BASE}/component-descriptors/github.com/my/component:1.0.0',
        f'{TARGET_BASE}/my-image:v1',
        f'{SOURCE_BASE}/some/source-image:latest',
    )
    repos = prune._repos_known(ctx)
    assert repos
    assert all(r.startswith(TARGET_BASE) for r in repos)


def test_known_repos_excludes_source_repos():
    ctx = _ctx_with_retain(
        f'{TARGET_BASE}/component-descriptors/github.com/my/component:1.0.0',
        f'{SOURCE_BASE}/some/source-image:latest',
    )
    repos = prune._repos_known(ctx)
    assert not any('europe-docker' in r for r in repos)


# ---------------------------------------------------------------------------
# full-registry prefix computation
# ---------------------------------------------------------------------------

def test_account_prefix_is_netloc_plus_account():
    assert prune._account_prefix(OCM_REPO) == TARGET_BASE


def test_full_registry_repo_refs_include_account():
    prefix = prune._account_prefix(OCM_REPO)
    repos = {f'{prefix}/{name}' for name in ['component-descriptors/foo', 'some-image']}
    assert all(r.startswith(TARGET_BASE + '/') for r in repos)


# ---------------------------------------------------------------------------
# scope predicate
# ---------------------------------------------------------------------------

def test_in_scope_rejects_external_ref():
    assert not prune._in_scope_of_ocm_repo(f'{SOURCE_BASE}/some-image:v1', OCM_REPO)


def test_in_scope_accepts_target_ref():
    assert prune._in_scope_of_ocm_repo(f'{TARGET_BASE}/some-image:v1', OCM_REPO)


# ---------------------------------------------------------------------------
# _iter_resource_refs
# ---------------------------------------------------------------------------

def _make_oci_node(image_ref):
    access = ocm.OciAccess(imageReference=image_ref)
    resource = types.SimpleNamespace(access=access)
    return types.SimpleNamespace(resource=resource)


def _make_relative_node(reference):
    access = ocm.RelativeOciAccess(reference=reference)
    resource = types.SimpleNamespace(access=access)
    return types.SimpleNamespace(resource=resource)


def test_iter_resource_refs_oci_access():
    ref = f'{TARGET_BASE}/some-image:v1'
    assert list(prune._iter_resource_refs(_make_oci_node(ref), OCM_REPO)) == [ref]


def test_iter_resource_refs_relative_access():
    refs = list(prune._iter_resource_refs(_make_relative_node('some-image:v1'), OCM_REPO))
    assert refs == [f'{TARGET_BASE}/some-image:v1']


def test_iter_resource_refs_yields_external_refs():
    external = f'{SOURCE_BASE}/some-image:v1'
    refs = list(prune._iter_resource_refs(_make_oci_node(external), OCM_REPO))
    assert refs == [external]
