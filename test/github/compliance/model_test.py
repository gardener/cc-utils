import dataclasses

import github.compliance.model as gcm


@dataclasses.dataclass
class Component:
    name: str = 'component1'


@dataclasses.dataclass
class Artefact:
    name: str = 'artefact1'


def test_ScanResultGroupCollection_result_groups():
    # empty results
    srgc = gcm.ScanResultGroupCollection(
        results=(),
        issue_type='dont/care',
        classification_callback=None,
        findings_callback=None,
    )

    assert srgc.result_groups == ()

    # one group (same component-name/artefact-name)
    results = (
        gcm.ScanResult(
            component=Component(name='c1'),
            artifact=Artefact(name='a1'),
        ),
        gcm.ScanResult(
            component=Component(name='c1'),
            artifact=Artefact(name='a1'),
        ),
    )

    srgc = gcm.ScanResultGroupCollection(
        results=results,
        issue_type='dont/care',
        classification_callback=None,
        findings_callback=None,
    )

    assert len((res_groups := srgc.result_groups)) == 1

    assert tuple(results) == tuple(res_groups[0].results)

    # two groups (different component-name/artefact-name)
    results = (
        gcm.ScanResult(
            component=Component(name='c1'),
            artifact=Artefact(name='a1'),
        ),
        gcm.ScanResult(
            component=Component(name='c2'),
            artifact=Artefact(name='a2'),
        ),
    )

    srgc = gcm.ScanResultGroupCollection(
        results=results,
        issue_type='dont/care',
        classification_callback=None,
        findings_callback=None,
    )
    assert len(srgc.result_groups) == 2
