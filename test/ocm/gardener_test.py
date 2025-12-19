import pytest

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


def test_image_dict_from_image_dict_and_resource():
    assert og.image_dict_from_image_dict_and_resource(
        component_name='cname',
        image={
            'name': 'iname',
        },
        resource=res(),
    ) == {
        'name': 'iname',
        'repository': 'acme.org/image-ref',
        'sourceRepository': 'cname',
        'tag': 'tag',
    }

    # labels should be retained
    assert og.image_dict_from_image_dict_and_resource(
        component_name='cname',
        image={
            'name': 'iname',
            'labels': [{'name': 'some-label', 'value': 42}],
        },
        resource=res(),
    ) == {
        'name': 'iname',
        'repository': 'acme.org/image-ref',
        'sourceRepository': 'cname',
        'tag': 'tag',
        'labels': [{'name': 'some-label', 'value': 42}],
    }

    # target-version should be honoured
    assert og.image_dict_from_image_dict_and_resource(
        component_name='cname',
        image={
            'name': 'iname',
            'targetVersion': 'tversion',
        },
        resource=res(),
    ) == {
        'name': 'iname',
        'repository': 'acme.org/image-ref',
        'sourceRepository': 'cname',
        'tag': 'tag',
        'targetVersion': 'tversion',
    }


def test_iter_image_dicts_from_image_dicts_and_resources():
    # smoketest only as function mostly delegates
    result = tuple(
        og.iter_image_dicts_from_image_dicts_and_resources(
            images=[{
                'name': 'resource-name',
            }],
            component_name='acme.org/other-component',
            resources=[res()],
        )
    )

    assert len(result) == 1
    result, = result
    assert result == {
        'name': 'resource-name',
        'repository': 'acme.org/image-ref',
        'tag': 'tag',
        'sourceRepository': 'acme.org/other-component',
    }


def test_iter_oci_image_dicts_from_component():
    # smoketest only as function mostly delegates
    # important aspects to check:
    # -> should process component-references
    # -> should process component's resources

    referenced_component = ocm.Component(
        name='acme.org/referenced-component',
        version='dontcare',
        provider='acme.org',
        sources=(),
        repositoryContexts=(),
        componentReferences=(),
        resources=(
            res(name='referenced-component-res-name'),
        ),
    )

    root_component = ocm.Component(
        name='acme.org/c1',
        version='dontcare',
        repositoryContexts=(),
        provider='acme.org',
        sources=(),
        componentReferences=(
            ocm.ComponentReference(
                name='cref',
                componentName=referenced_component.name,
                version=referenced_component.version,
                labels=(
                    ocm.Label(
                        name='imagevector.gardener.cloud/images',
                        value={'images': [{
                            'name': 'referenced-component-res-name',
                        }]}
                    ),
                ),
            ),
        ),
        resources=(
            res(),
        ),
    )

    def lookup(cid):
        if cid != root_component.componentReferences[0]:
            pytest.fail(f'component-descriptor-lookup was called with unexpected {cid=}')

        return referenced_component

    # call as parameterised in first round of iter_oci_image_dicts_from_rooted_component
    result = og.iter_oci_image_dicts_from_component(
        component=root_component,
        resource_names_from_label=True,
        fallback_to_target_version_from_resource=False,
        resource_names=None,
        component_descriptor_lookup=lookup,
    )
    result = tuple(result)

    assert len(result) == 2

    assert result == (
        { # this image-dict is yielded from root_component's sub-component-reference
            'name': 'referenced-component-res-name',
            'repository': 'acme.org/image-ref',
            'tag': 'tag',
            'sourceRepository': 'acme.org/referenced-component',
        },
        { # this image-dict is yielded from root-component's resource
            'name': 'name-from-label',
            'repository': 'acme.org/image-ref',
            'tag': 'tag',
            'targetVersion': 'version-from-label',
        }
    )
