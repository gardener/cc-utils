import os
import sys

own_dir = os.path.dirname(__file__)
repo_root = os.path.join(own_dir, '../../..')
sys.path.insert(1, repo_root)

import datetime
import dataclasses
import pytest

import yaml

import base_component_descriptor as bcd
import ocm


def test_load_base_component(tmp_path):
    absent_path = os.path.join(tmp_path, 'does-not-exist')

    # expect empty base-component for absent file
    component = bcd.load_base_component(absent_path, absent_ok=True)

    assert component.name is None
    assert component.version is None
    assert component.repositoryContexts == []
    assert component.resources == []
    assert component.sources == []
    assert component.labels == []
    assert component.main_source == {}

    with pytest.raises(SystemExit):
        with open(path := os.path.join(tmp_path, 'base-component.yaml'), 'w') as f:
            yaml.safe_dump({'version': 'not-allowed'}, f)
        bcd.load_base_component(path)


def test_fill_in_defaults():
    dummy = {}
    dummy_access = {'type': 'dummy'}
    dummy_source = ocm.Source(
        name='source',
        access=dummy_access,
    )
    component = bcd.BaseComponent(
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
    assert component.creationTime == 'some-time'

    # check creation-time is correctly formatted
    component.creationTime = None
    component = bcd.fill_in_defaults(
        component=component,
        name='other-name',
        provider='other-provider',
        ocm_repo='other-ocm-repo',
        main_source=dummy_source2,
        creation_time=dummy_time2,
    )

    assert component.creationTime == dummy_time2.strftime('%Y-%m-%dT%H:%M:%SZ')


def test_as_component_descriptor_dict():
    dummy = {}
    component = bcd.BaseComponent(
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
