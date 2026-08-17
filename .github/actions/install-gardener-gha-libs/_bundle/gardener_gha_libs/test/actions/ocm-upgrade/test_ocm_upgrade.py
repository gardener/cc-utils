# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import sys
import os
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../.github/actions/ocm-upgrade'))

import ocm
import ocm.gardener
import ocm_upgrade


def test_find_upgrade_vector_newer_available():
    cid = ocm.ComponentIdentity(
        name='example.com/comp',
        version='1.0.0',
    )

    vector = ocm.gardener.find_upgrade_vector(
        component_id=cid,
        version_lookup=lambda _: ['1.0.0', '1.1.0', '2.0.0'],
    )

    assert vector is not None
    assert vector.whence.version == '1.0.0'
    assert vector.whither.version == '2.0.0'


def test_find_upgrade_vector_already_latest():
    cid = ocm.ComponentIdentity(
        name='example.com/comp',
        version='2.0.0',
    )

    vector = ocm.gardener.find_upgrade_vector(
        component_id=cid,
        version_lookup=lambda _: ['1.0.0', '2.0.0'],
    )

    assert vector is None


def test_find_upgrade_vector_ignores_prerelease():
    cid = ocm.ComponentIdentity(
        name='example.com/comp',
        version='1.0.0',
    )

    vector = ocm.gardener.find_upgrade_vector(
        component_id=cid,
        version_lookup=lambda _: ['1.0.0', '1.1.0-dev'],
        ignore_prerelease_versions=True,
    )

    assert vector is None


def _make_cref(name, component_name, ver):
    return ocm.ComponentReference(
        name=name,
        componentName=component_name,
        version=ver,
    )


def test_upstream_duplicate_crefs_picks_greatest_version():
    '''
    Regression test: greatest_component_reference_version must return the
    greatest version across all refs matching a componentName, not the first.

    Mirrors the gardenlinux/landscape bug: upstream had 1877.14 listed before
    2150.2.0; the old first-match code would have returned 1877.14.
    '''
    gl_name = 'example.com/gardenlinux'
    crefs = [
        _make_cref('gardenlinux', gl_name, '1877.14'),  # comes first (old bug trigger)
        _make_cref('gardenlinux', gl_name, '2150.2.0'),
    ]

    result = ocm.gardener.greatest_component_reference_version(
        references=crefs,
        component_name=gl_name,
    )

    assert result == '2150.2.0'


def test_upstream_no_matching_cref_returns_none():
    crefs = [_make_cref('other', 'example.com/other', '1.0.0')]

    result = ocm.gardener.greatest_component_reference_version(
        references=crefs,
        component_name='example.com/gardenlinux',
    )

    assert result is None


# --- ComponentPolicy tests ---

def test_component_policy_matches_exact():
    policy = ocm_upgrade.ComponentPolicy(components=['example.com/foo'])
    assert policy.matches('example.com/foo')
    assert not policy.matches('example.com/bar')


def test_component_policy_matches_regex():
    policy = ocm_upgrade.ComponentPolicy(components=['example\\.com/.*'])
    assert policy.matches('example.com/foo')
    assert policy.matches('example.com/bar')
    assert not policy.matches('other.org/foo')


def test_component_policy_normalises_string_to_list():
    policy = ocm_upgrade.ComponentPolicy(components='example.com/foo')
    assert policy.components == ['example.com/foo']
    assert policy.matches('example.com/foo')


def test_component_policy_from_dict_full():
    raw = {
        'components': ['example.com/foo'],
        'upstream-component-name': 'example.com/upstream',
        'upstream-update-policy': 'accept-hotfixes',
    }
    policy = ocm_upgrade.ComponentPolicy.from_dict(raw)
    assert policy.components == ['example.com/foo']
    assert policy.upstream_component_name == 'example.com/upstream'
    assert policy.upstream_update_policy is ocm_upgrade.UpstreamUpdatePolicy.ACCEPT_HOTFIXES


def test_component_policy_from_dict_empty_upstream_disables_gating():
    raw = {
        'components': ['example.com/foo'],
        'upstream-component-name': '',
    }
    policy = ocm_upgrade.ComponentPolicy.from_dict(raw)
    assert policy.upstream_component_name == ''


def test_component_policy_from_dict_minimal():
    policy = ocm_upgrade.ComponentPolicy.from_dict({'components': ['example.com/foo']})
    assert policy.upstream_component_name is None
    assert policy.upstream_update_policy is None


# --- create_upgrade_pullrequests behavioural tests ---

def _make_component(name, version, crefs):
    return ocm.Component(
        name=name,
        version=version,
        componentReferences=crefs,
        resources=[],
        sources=[],
        repositoryContexts=[],
        provider='test',
    )


def _run_create_upgrade_pullrequests(**kwargs):
    '''Call create_upgrade_pullrequests with mocked GitHub/git layers.'''
    created = []

    def fake_create_upgrade_pullrequest(upgrade_vector, **_kw):
        created.append(upgrade_vector)
        return unittest.mock.MagicMock()

    mock_repository = unittest.mock.MagicMock()

    with (
        unittest.mock.patch.object(
            ocm_upgrade, 'create_upgrade_pullrequest', fake_create_upgrade_pullrequest,
        ),
        unittest.mock.patch('github.pullrequest.iter_upgrade_pullrequests', return_value=iter([])),
        unittest.mock.patch('github.pullrequest.reset_worktree'),
        unittest.mock.patch('gitutil.GitHelper'),
    ):
        list(ocm_upgrade.create_upgrade_pullrequests(
            upgrade_pullrequests=[],
            repo_dir='/tmp',
            repo_url='https://github.com/example/repo',
            repository=mock_repository,
            merge_policy=ocm_upgrade.MergePolicy.MANUAL,
            merge_method=ocm_upgrade.MergeMethod.MERGE,
            merge_policy_configs=[],
            branch='main',
            oci_client=unittest.mock.MagicMock(),
            pr_naming_pattern='component-name',
            **kwargs,
        ))

    return created


def _make_upstream_lookup(upstream_name, upstream_crefs):
    '''Returns (component_descriptor_lookup, version_lookup) where upstream has given crefs.'''
    upstream_component = _make_component(upstream_name, '1.0.0', upstream_crefs)
    upstream_cd = ocm.ComponentDescriptor(
        meta=ocm.Metadata(),
        component=upstream_component,
    )

    def component_descriptor_lookup(cid, absent_ok=False):
        if cid.name == upstream_name:
            return upstream_cd
        raise ValueError(f'unexpected lookup: {cid}')

    return component_descriptor_lookup


def test_upstream_gating_skips_component_not_in_upstream():
    '''Existing behaviour: component absent from upstream is skipped.'''
    component = _make_component(
        'example.com/my-comp', '1.0.0',
        [_make_cref('dep', 'example.com/dep', '1.0.0')],
    )
    cd_lookup = _make_upstream_lookup('example.com/upstream', [])  # upstream has no refs

    created = _run_create_upgrade_pullrequests(
        component=component,
        component_descriptor_lookup=cd_lookup,
        version_lookup=lambda _: ['1.0.0', '2.0.0'],
        upstream_component_name='example.com/upstream',
    )

    assert created == []


def test_component_policy_empty_upstream_uses_find_latest():
    '''
    Component with upstream-component-name='' in component_policies falls back
    to find-latest instead of being skipped.
    '''
    component = _make_component(
        'example.com/my-comp', '1.0.0',
        [_make_cref('dep', 'example.com/dep', '1.0.0')],
    )
    cd_lookup = _make_upstream_lookup('example.com/upstream', [])  # upstream has no refs

    policies = [
        ocm_upgrade.ComponentPolicy(
            components=['example.com/dep'],
            upstream_component_name='',
        )
    ]

    created = _run_create_upgrade_pullrequests(
        component=component,
        component_descriptor_lookup=cd_lookup,
        version_lookup=lambda _: ['1.0.0', '2.0.0'],
        upstream_component_name='example.com/upstream',
        component_policies=policies,
    )

    assert len(created) == 1
    assert created[0].whence.name == 'example.com/dep'
    assert created[0].whither.version == '2.0.0'


def test_component_policy_upstream_override():
    '''component_policies can gate a component against a different upstream.'''
    gated_cref = _make_cref('gated', 'example.com/gated', '1.0.0')
    component = _make_component('example.com/my-comp', '1.0.0', [gated_cref])

    alt_upstream_crefs = [_make_cref('gated', 'example.com/gated', '1.5.0')]
    alt_upstream = _make_component('example.com/alt-upstream', '1.0.0', alt_upstream_crefs)
    alt_upstream_cd = ocm.ComponentDescriptor(meta=ocm.Metadata(), component=alt_upstream)

    def cd_lookup(cid, absent_ok=False):
        if cid.name == 'example.com/alt-upstream':
            return alt_upstream_cd
        raise ValueError(f'unexpected lookup: {cid}')

    policies = [
        ocm_upgrade.ComponentPolicy(
            components=['example.com/gated'],
            upstream_component_name='example.com/alt-upstream',
        )
    ]

    created = _run_create_upgrade_pullrequests(
        component=component,
        component_descriptor_lookup=cd_lookup,
        version_lookup=lambda _: ['1.0.0', '1.5.0', '2.0.0'],
        upstream_component_name='example.com/main-upstream',  # global — not used for this cref
        component_policies=policies,
    )

    assert len(created) == 1
    assert created[0].whither.version == '1.5.0'  # gated to alt-upstream, not latest (2.0.0)
