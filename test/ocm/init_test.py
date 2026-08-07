import ocm


def test_access_type_aliases():
    aliases = {
        'GitHub': ocm.AccessType.GITHUB,
        'gitHub': ocm.AccessType.GITHUB,
        'github': ocm.AccessType.GITHUB,
        'Helm': ocm.AccessType.HELM,
        'helm': ocm.AccessType.HELM,
        'LocalBlob': ocm.AccessType.LOCAL_BLOB,
        'localBlob': ocm.AccessType.LOCAL_BLOB,
        'NPM': ocm.AccessType.NPM,
        'npm': ocm.AccessType.NPM,
        'OCIImage': ocm.AccessType.OCI_REGISTRY,
        'ociArtifact': ocm.AccessType.OCI_REGISTRY,
        'OCIRegistry': ocm.AccessType.OCI_REGISTRY,
        'ociRegistry': ocm.AccessType.OCI_REGISTRY,
        'OCIImageLayer': ocm.AccessType.OCI_BLOB,
        'ociBlob': ocm.AccessType.OCI_BLOB,
        'S3': ocm.AccessType.S3,
        's3': ocm.AccessType.S3,
        'Wget': ocm.AccessType.WGET,
        'wget': ocm.AccessType.WGET,
    }
    for alias, expected in aliases.items():
        assert ocm.AccessType(alias) is expected, f'{alias} should map to {expected}'


def test_artefact_type_aliases():
    aliases = {
        'blob': ocm.ArtefactType.BLOB,
        'executable': ocm.ArtefactType.EXECUTABLE,
        'directoryTree': ocm.ArtefactType.DIRECTORY_TREE,
        'filesystem': ocm.ArtefactType.DIRECTORY_TREE,
        'git': ocm.ArtefactType.GIT,
        'helmChart': ocm.ArtefactType.HELM_CHART,
        'npmPackage': ocm.ArtefactType.NPM_PACKAGE,
        'ociArtifact': ocm.ArtefactType.OCI_ARTEFACT,
        'ociImage': ocm.ArtefactType.OCI_IMAGE,
        'sbom': ocm.ArtefactType.SBOM,
    }
    for alias, expected in aliases.items():
        assert ocm.ArtefactType(alias) is expected, f'{alias} should map to {expected}'


def test_strip_version_during_deserialisation():
    cd_dict = {
        'meta': {'schemaVersion': 'v2'},
        'component': {
            'name': 'acme.org/foo/bar',
            'version': '1.0.0',
            'provider': 'acme.org',
            'repositoryContexts': [{'baseUrl': 'ghcr.io/test', 'type': 'ociRegistry'}],
            'componentReferences': [],
            'labels': [],
            'sources': [],
            'resources': [{
                'name': 'helm-res',
                'version': '1.0.0',
                'type': 'helmChart/v1',
                'relation': 'local',
                'labels': [],
                'srcRefs': [],
                'extraIdentity': {},
                'access': {'type': 'Helm/v1', 'helmRepository': 'oci://repo', 'helmChart': 'chart'},
            }],
        },
    }
    cd = ocm.ComponentDescriptor.from_dict(cd_dict)
    resource = cd.component.resources[0]
    assert resource.type is ocm.ArtefactType.HELM_CHART
    assert isinstance(resource.access, ocm.HelmAccess)


def test_new_access_classes_instantiate():
    helm = ocm.HelmAccess(helmRepository='oci://repo', helmChart='mychart')
    assert helm.type is ocm.AccessType.HELM

    npm = ocm.NPMAccess(registry='https://registry.npmjs.org', package='lodash', version='4.17.21')
    assert npm.type is ocm.AccessType.NPM

    wget = ocm.WgetAccess(url='https://example.com/file.tar.gz')
    assert wget.type is ocm.AccessType.WGET


def test_s3_access_has_correct_type():
    s3 = ocm.S3Access(bucket='my-bucket', key='my/key')
    assert s3.type == 's3/v1'
    s3 = ocm.LegacyS3Access(bucketName='my-bucket', objectKey='my/key')
    assert s3.type == 's3/v2'
