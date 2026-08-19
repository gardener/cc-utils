'''
Regression tests for ocm.prune — scoping fixes that ensure pruning never
touches artefacts outside the target OCM repository.
'''
import types

import ocm
import ocm.prune as prune


TARGET_BASE = 'keppel.example.com/my-account'
SOURCE_BASE = 'europe-docker.pkg.dev/other-project/releases-public'

RETAIN_IN_TARGET = {
    f'{TARGET_BASE}/component-descriptors/github.com/my/component:1.0.0',
    f'{TARGET_BASE}/my-image:v1',
}
RETAIN_IN_SOURCE = {
    f'{SOURCE_BASE}/some/source-image:latest',
    f'{SOURCE_BASE}/another/image:sha256-abc123.sig',
}
RETAIN_SET = frozenset(RETAIN_IN_TARGET | RETAIN_IN_SOURCE)


def _ocm_repo(base_url=TARGET_BASE):
    return ocm.OciOcmRepository(baseUrl=base_url)


# --- known-repositories -------------------------------------------------------

def test_known_repositories_only_enumerates_target_repos(monkeypatch):
    enumerated = []

    def fake_discover(repos, retain_set, oci_client, jobs):
        enumerated.extend(repos)
        return []

    monkeypatch.setattr(prune, '_discover_candidates', fake_discover)

    prune.iter_candidates_known_repositories(
        retain_set=RETAIN_SET,
        oci_client=None,
        ocm_repo=_ocm_repo(),
    )

    for repo in enumerated:
        assert repo.startswith(TARGET_BASE), (
            f'enumerated repo {repo!r} is outside target base {TARGET_BASE!r}'
        )

    # At least the in-target repos should be present
    assert any(repo.startswith(TARGET_BASE) for repo in enumerated)


def test_known_repositories_does_not_enumerate_source_repos(monkeypatch):
    enumerated = []

    def fake_discover(repos, retain_set, oci_client, jobs):
        enumerated.extend(repos)
        return []

    monkeypatch.setattr(prune, '_discover_candidates', fake_discover)

    prune.iter_candidates_known_repositories(
        retain_set=RETAIN_SET,
        oci_client=None,
        ocm_repo=_ocm_repo(),
    )

    source_repos = [r for r in enumerated if 'europe-docker' in r]
    assert not source_repos, (
        f'source repos should not be enumerated, got: {source_repos}'
    )


# --- full-registry ------------------------------------------------------------

def test_full_registry_only_enumerates_target_account(monkeypatch):
    enumerated_prefixes = []

    def fake_iter_repositories(client, image_reference, raise_if_unsupported):
        enumerated_prefixes.append(str(image_reference))
        return iter([])

    monkeypatch.setattr(prune.oci.nonstd, 'iter_repositories', fake_iter_repositories)

    prune.iter_candidates_full_registry(
        retain_set=RETAIN_SET,
        oci_client=None,
        ocm_repo=_ocm_repo(),
    )

    for prefix in enumerated_prefixes:
        assert 'europe-docker' not in prefix, (
            f'full-registry enumeration reached source registry: {prefix!r}'
        )
    assert any('keppel.example.com' in p for p in enumerated_prefixes)


def test_full_registry_repo_refs_include_account(monkeypatch):
    '''Keppel API returns repo names without the account prefix; the constructed
    OCI refs must include it (e.g. keppel.../account/repo, not keppel.../repo).'''
    collected_repos = []

    def fake_iter_repositories(client, image_reference, raise_if_unsupported):
        # simulate Keppel returning bare names without the account prefix
        return iter(['component-descriptors/foo', 'some-image'])

    def fake_discover(repos, retain_set, oci_client, jobs):
        collected_repos.extend(repos)
        return []

    monkeypatch.setattr(prune.oci.nonstd, 'iter_repositories', fake_iter_repositories)
    monkeypatch.setattr(prune, '_discover_candidates', fake_discover)

    prune.iter_candidates_full_registry(
        retain_set=RETAIN_SET,
        oci_client=None,
        ocm_repo=_ocm_repo(),
    )

    for repo in collected_repos:
        assert repo.startswith(TARGET_BASE + '/'), (
            f'repo {repo!r} is missing the account prefix {TARGET_BASE!r}'
        )


# --- _iter_resource_refs scoping ---------------------------------------------

def _make_resource_node(image_ref):
    '''Build a minimal ResourceNode-like object with OciAccess.'''
    access = ocm.OciAccess(imageReference=image_ref)
    resource = types.SimpleNamespace(access=access)
    return types.SimpleNamespace(resource=resource)


def test_iter_resource_refs_skips_out_of_scope_oci_access():
    '''OciAccess refs outside ocm_repo must not be yielded.'''
    node = _make_resource_node(f'{SOURCE_BASE}/some-image:v1')
    refs = list(prune._iter_resource_refs(node, _ocm_repo()))
    assert refs == [], f'expected no refs, got: {refs}'


def test_iter_resource_refs_yields_in_scope_oci_access():
    '''OciAccess refs within ocm_repo must be yielded.'''
    in_scope = f'{TARGET_BASE}/some-image:v1'
    node = _make_resource_node(in_scope)
    refs = list(prune._iter_resource_refs(node, _ocm_repo()))
    assert refs == [in_scope]
