import os
import pytest

import yaml

import ocm.base_component


def test_load_base_component(tmp_path):
    absent_path = os.path.join(tmp_path, 'does-not-exist')

    # expect empty base-component for absent file
    component = ocm.base_component.load_base_component(absent_path, absent_ok=True)

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
        ocm.base_component.load_base_component(path)
