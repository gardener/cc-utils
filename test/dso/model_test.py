import dso.model


def test_artefact_metadata_id():
    artefact_metadatum_1 = dso.model.ArtefactMetadata(
        artefact=dso.model.ComponentArtefactId(
            component_name='my-component',
            component_version=None,
            artefact_kind=dso.model.ArtefactKind.RESOURCE,
            artefact=dso.model.LocalArtefactId(
                artefact_name='my-artefact',
                artefact_version='0.1.0',
                artefact_type='my-artefact-type',
                artefact_extra_id={
                    'extra-identity-key-1': 'extra-identity-value-1',
                    'extra-identity-key-2': 'extra-identity-value-2',
                },
            ),
        ),
        meta=dso.model.Metadata(
            datasource='my-datasource',
            type='my-type',
        ),
        data={},
    )

    artefact_metadatum_2 = dso.model.ArtefactMetadata(
        artefact=dso.model.ComponentArtefactId(
            component_name='my-component',
            component_version='0.1.0',
            artefact=dso.model.LocalArtefactId(
                artefact_name='my-artefact',
                artefact_version='0.1.0',
                artefact_type='my-artefact-type',
            ),
            references=[artefact_metadatum_1.artefact],
        ),
        meta=dso.model.Metadata(
            datasource='my-datasource',
            type='my-type',
        ),
        data={},
    )

    assert artefact_metadatum_1.id == 'de25c7bf37c6031b6d38ef14288b0e2b'
    assert artefact_metadatum_2.id == 'e7645c972e93cdf01526df135253584d'
