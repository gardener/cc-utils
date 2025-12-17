import ocm
import ocm.gardener as og


def test_eval_version_template():
    assert og.eval_version_template(
        version_template=ocm.gardener.VersionTemplate.from_dict({
            'type': 'jq',
            'expr': '."attr-1"',
        }),
        image_dict={
            'attr-1': 'foo',
            'attr-2': 'ignoreme',
        },
    ) == 'foo'


def res(
    name='resource-name',
    version='1.2.3',
    type=ocm.ArtefactType.OCI_IMAGE,
    access=ocm.OciAccess(
        imageReference='acme.org/image-ref:tag',
    ),
    labels=(
        ocm.Label(
            name='imagevector.gardener.cloud/name',
            value='name-from-label',
        ),
        ocm.Label(
            name='imagevector.gardener.cloud/target-version',
            value='version-from-label',
        ),
    )
) -> ocm.Resource:
    return ocm.Resource(
        name=name,
        version=version,
        type=type,
        access=access,
        labels=labels,
    )


def test_oci_image_dict_from_resource():
    # using defaults
    assert og.oci_image_dict_from_resource(
        resource=res(),
        # resource_names_from_label=True,
        # fallback_to_target_version_from_resource=False,
        # resource_names=None,
    ) == {
        'name': 'name-from-label',
        'repository': 'acme.org/image-ref',
        'tag': 'tag',
        'targetVersion': 'version-from-label',
    }

    # should not fallback if label is present
    assert og.oci_image_dict_from_resource(
        resource=res(),
        # resource_names_from_label=True,
        fallback_to_target_version_from_resource=True,
        # resource_names=None,
    ) == {
        'name': 'name-from-label',
        'repository': 'acme.org/image-ref',
        'tag': 'tag',
        'targetVersion': 'version-from-label',
    }

    assert og.oci_image_dict_from_resource(
        resource=res(
            labels=(
                ocm.Label(
                    name='imagevector.gardener.cloud/name',
                    value='name-from-label',
                ),
            ),
        ),
        # resource_names_from_label=True,
        fallback_to_target_version_from_resource=True,
        # resource_names=None,
    ) == {
        'name': 'name-from-label',
        'repository': 'acme.org/image-ref',
        'tag': 'tag',
        'targetVersion': '1.2.3',
    }

    # retain resource-name (ignore label)
    assert og.oci_image_dict_from_resource(
        resource=res(),
        resource_names_from_label=False,
        # fallback_to_target_version_from_resource=False,
        # resource_names=None,
    ) == {
        'name': 'resource-name',
        'repository': 'acme.org/image-ref',
        'tag': 'tag',
        'targetVersion': 'version-from-label',
    }
