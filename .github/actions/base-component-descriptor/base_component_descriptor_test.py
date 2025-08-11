import os
import sys

own_dir = os.path.dirname(__file__)
repo_root = os.path.join(own_dir, '../../..')
sys.path.insert(1, repo_root)

import datetime
import dataclasses

import base_component_descriptor as bcd
import ocm
import ocm.base_component


BaseComponent = ocm.base_component.BaseComponent


def test_fill_in_defaults():
    dummy = {}
    dummy_access = {'type': 'dummy'}
    dummy_source = ocm.Source(
        name='source',
        access=dummy_access,
    )
    component = BaseComponent(
        name='name',
        version='version',
        repositoryContexts=[dummy],
        provider='acme',
        componentReferences=[dummy],
        resources=[dummy],
        sources=[dummy],
        labels=[dummy],
        creationTime='some-time',
        main_source=dummy_source,
        componentPrefixes=['foo'],
    )

    # check that existing values are _not_ overwritten
    dummy_access2 = {'type': 'dummy2'}
    dummy_source2 = ocm.Source(
        name='other-source',
        access=dummy_access2,
    )
    dummy_time2 = datetime.datetime.now()

    component = bcd.fill_in_defaults(
        component=component,
        name='other-name',
        version='other-version',
        provider='other-provider',
        ocm_repo='other-ocm-repo',
        main_source=dummy_source2,
        creation_time=dummy_time2,
    )

    assert component.name == 'name'
    assert component.version == 'version'
    assert component.repositoryContexts == [dummy]
    assert component.provider == 'acme'
    assert component.componentReferences == [dummy]
    assert component.resources == [dummy]
    assert component.sources == [dummy, dummy_source]
    assert component.main_source == dummy_source
    assert component.componentPrefixes == ['foo']
    assert component.creationTime == 'some-time'

    # check creation-time is correctly formatted
    component.creationTime = None
    component = bcd.fill_in_defaults(
        component=component,
        name='other-name',
        version='other-version',
        provider='other-provider',
        ocm_repo='other-ocm-repo',
        main_source=dummy_source2,
        creation_time=dummy_time2,
    )

    assert component.creationTime == dummy_time2.strftime('%Y-%m-%dT%H:%M:%SZ')


def test_as_component_descriptor_dict():
    dummy = {}
    component = BaseComponent(
        name='name',
        version='version',
        repositoryContexts=[dummy],
        provider='acme',
        componentReferences=[dummy],
        resources=[dummy],
        sources=[dummy],
        labels=[dummy],
        creationTime='creation-time',
        main_source=dummy,
        componentPrefixes=['foo'],
    )

    res = bcd.as_component_descriptor_dict(
        component=component,
    )

    assert res['meta'] == dataclasses.asdict(ocm.Metadata())
    assert set(res.keys()) == {'meta', 'component'}

    c = res['component']

    assert c == {
        'componentReferences': [dummy],
        'creationTime': 'creation-time',
        'labels': [dummy],
        'name': 'name',
        'provider': 'acme',
        'repositoryContexts': [dummy],
        'resources': [dummy],
        'sources': [dummy],
        'version': 'version',
    }


def test_add_resources_from_imagevector():
    local_resource = ocm.Resource(
        name='apiserver',
        version='version',
        type=ocm.ArtefactType.OCI_IMAGE,
        access=ocm.OciAccess(
            imageReference='europe-docker.pkg.dev/gardener-project/releases/gardener/apiserver',
        ),
    )
    component = BaseComponent(
        name='github.com/gardener/gardener',
        version='version',
        repositoryContexts=[],
        provider='acme',
        componentReferences=[],
        resources=[
        ],
        sources=[],
        labels=[],
        creationTime='creation-time',
        main_source={},
    )

    assert component.resources == []

    component = bcd.add_resources_from_imagevector(
        imagevector_file=os.path.join(own_dir, 'imagevector-test.yaml'),
        component=component,
        component_prefixes=[
            'europe-docker.pkg.dev/gardener-project/releases',
            'some-other-prefix',
        ],
    )

    # local resources are expected to be added later-on by pipeline (oci-ocm-action in our case),
    # hence, we expect the resource to be _removed_
    assert local_resource not in component.resources

    # in our imagevector-test.yaml, we have a total of:
    # - 1 resources to be ignored
    # - 1 resources to be added
    # - 1 component-references to be added

    assert len(component.resources) == 1
    assert len(component.componentReferences) == 1

    # check resource from imagevector
    resource = component.resources[0]

    assert resource.name == 'pause-container'
    assert resource.version == '3.10'
    assert resource.access.imageReference == 'registry.k8s.io/pause:3.10'

    cref = component.componentReferences[0]

    assert cref.name == 'gardener-dashboard'
    assert cref.componentName == 'github.com/gardener/dashboard'
    assert cref.version == '1.80.2'
