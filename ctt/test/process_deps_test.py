# SPDX-FileCopyrightText: 2021 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import ctt.process_dependencies as process_dependencies
import sbom.inject as sbom_inject
import ocm


def test_processor_instantiation(tmpdir):
    tmpfile = tmpdir.join('a_file')
    tmpfile.write('')  # touch

    cfg = {
        'target': {
            'type': 'RegistriesTarget',
            'kwargs': {
                'registries': ['foo'],
            },
        },
        'filter': {
            'type': 'ImageFilter',
            'kwargs': {
                'include_image_refs': ['^aaa'],
            },
        },
        'processor': {
            'type': 'FileFilter',
            'kwargs': {
                'filter_files': [tmpfile],
            },
        },
        'upload': {
            'type': 'PrependTargetUploader',
        },
    }

    _ = process_dependencies.processing_pipeline(cfg)

    # test shared target
    shared_target = {'shared_target': cfg['target']}
    cfg['target'] = 'shared_target'

    _ = process_dependencies.processing_pipeline(cfg, shared_targets=shared_target)

    # revert
    cfg['target'] = shared_target['shared_target']

    # test shared processor
    shared_proc = {'shared_p': cfg['processor']}
    cfg['processor'] = 'shared_p'

    _ = process_dependencies.processing_pipeline(cfg, shared_processors=shared_proc)

    # revert
    cfg['processor'] = shared_proc['shared_p']

    # test shared uploader
    shared_upld = {'shared_u': cfg['upload']}
    cfg['upload'] = 'shared_u'

    _ = process_dependencies.processing_pipeline(cfg, shared_uploaders=shared_upld)


def _fake_resource(name, version='1.0', extra_identity=None):
    return ocm.Resource(
        name=name,
        version=version,
        type='ociImage',
        relation=ocm.ResourceRelation.EXTERNAL,
        access=ocm.OciAccess(imageReference=f'registry.example.com/{name}:{version}'),
        extraIdentity=extra_identity or {},
    )


def test_build_sbom_ocm_resources_distinct_identity_for_same_name():
    '''
    Two source resources sharing a name but different extraIdentity must produce
    SBOM resources with distinct OCM identities.
    '''
    common_kwargs = dict(
        version='1.0',
        source_image_ref='registry.example.com/foo:1.0',
        source_digest='sha256:aabbcc',
        repo_ref='registry.example.com/foo',
        spdx_referrer_digest='sha256:spdx1',
        cdx_referrer_digest='sha256:cdx1',
        tool_ver='1.0.0',
    )

    spdx_amd64, cdx_amd64 = sbom_inject.build_sbom_ocm_resources(
        resource_name='hyperkube',
        source_extra_identity={'arch': 'amd64'},
        **common_kwargs,
    )
    spdx_arm64, cdx_arm64 = sbom_inject.build_sbom_ocm_resources(
        resource_name='hyperkube',
        source_extra_identity={'arch': 'arm64'},
        **common_kwargs,
    )

    all_resources = [spdx_amd64, cdx_amd64, spdx_arm64, cdx_arm64]

    # each resource must have a unique identity among its peers
    identities = [r.identity(peers=all_resources) for r in all_resources]
    assert len(set(map(str, identities))) == 4, (
        f'expected 4 distinct identities, got: {identities}'
    )

    # arch must be present in extra_identity
    assert spdx_amd64.extraIdentity.get('arch') == 'amd64'
    assert spdx_arm64.extraIdentity.get('arch') == 'arm64'
    assert spdx_amd64.extraIdentity.get('sbom-format') == 'spdx-2.3'
    assert cdx_amd64.extraIdentity.get('sbom-format') == 'cyclonedx-1.6'


def test_build_sbom_ocm_resources_no_source_extra_identity():
    '''Without source_extra_identity the only distinguisher is sbom-format.'''
    spdx, cdx = sbom_inject.build_sbom_ocm_resources(
        resource_name='myimage',
        version='2.0',
        source_image_ref='registry.example.com/myimage:2.0',
        source_digest='sha256:ddeeff',
        repo_ref='registry.example.com/myimage',
        spdx_referrer_digest='sha256:spdxref',
        cdx_referrer_digest='sha256:cdxref',
        tool_ver=None,
    )
    assert spdx.extraIdentity == {'version': '2.0', 'sbom-format': 'spdx-2.3'}
    assert cdx.extraIdentity == {'version': '2.0', 'sbom-format': 'cyclonedx-1.6'}
    assert spdx.identity(peers=[spdx, cdx]) != cdx.identity(peers=[spdx, cdx])


def test_build_sbom_ocm_resources_version_and_format_both_unique():
    '''
    Same name, different versions, no source extraIdentity: SPDX and CycloneDX
    resources for the same source version must have distinct identities even
    after the version-fallback fires (because other versions trigger it).

    This is the scenario that caused the LSS release failure (run 6894333):
    many hyperkube resources at different k8s versions produce SBOM resources
    all named 'hyperkube' with extraIdentity={sbom-format: ...}.  The
    version-fallback must preserve sbom-format so SPDX and CycloneDX at the
    same version remain distinguishable.
    '''
    def _make(version):
        return sbom_inject.build_sbom_ocm_resources(
            resource_name='hyperkube',
            version=version,
            source_image_ref=f'registry.example.com/hyperkube:{version}',
            source_digest='sha256:aabbcc',
            repo_ref='registry.example.com/hyperkube',
            spdx_referrer_digest='sha256:spdxref',
            cdx_referrer_digest='sha256:cdxref',
            tool_ver=None,
        )

    spdx_135, cdx_135 = _make('1.35.3')
    spdx_133, cdx_133 = _make('1.33.8')

    all_resources = [spdx_135, cdx_135, spdx_133, cdx_133]
    identities = [r.identity(peers=all_resources) for r in all_resources]

    assert len(set(map(str, identities))) == 4, (
        f'expected 4 distinct identities, got duplicates: {[str(i) for i in identities]}'
    )


def test_identity_version_fallback_with_version_in_extra_identity():
    '''
    version-fallback must not raise TypeError when a resource already has
    'version' in its extraIdentity (unusual but valid in the wild).
    '''
    r1 = _fake_resource('foo', version='1.0', extra_identity={'version': 'v2', 'arch': 'amd64'})
    r2 = _fake_resource('foo', version='1.0', extra_identity={'version': 'v2', 'arch': 'arm64'})
    # triggers version-fallback: same name+extraIdentity base among peers
    r3 = _fake_resource('foo', version='2.0', extra_identity={'version': 'v2', 'arch': 'amd64'})

    all_resources = [r1, r2, r3]
    # must not raise
    identities = [r.identity(peers=all_resources) for r in all_resources]
    assert len(set(map(str, identities))) == 3, (
        f'expected 3 distinct identities, got: {[str(i) for i in identities]}'
    )
