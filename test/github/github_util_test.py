# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import dataclasses
import functools
import pytest

import github.util as ghu

import ocm


# test gear
pull_request_mock = object() # keep this as simple as possible for now
create_upgrade_pr = functools.partial(
    ghu.UpgradePullRequest,
    pull_request=pull_request_mock,
)


def test_ctor():
    # upgrade component
    create_upgrade_pr(
        from_ref=ocm.ComponentReference(
            name='abcd', componentName='a.b/c1', version='1.2.3'
        ),
        to_ref=ocm.ComponentReference(
            name='abcd', componentName='a.b/c1', version='2.0.0'
        ),
    )
    # upgrade web dependency
    create_upgrade_pr(
        from_ref=ocm.Resource(
            name='dep_red',
            version='1.2.3',
            type=ocm.ArtefactType.BLOB,
            access=None,
        ),
        to_ref=ocm.Resource(
            name='dep_red',
            version='2.0.0',
            type=ocm.ArtefactType.BLOB,
            access=None,
        ),
    )
    # error: mismatch in dependency name
    with pytest.raises(ValueError, match='reference name mismatch'):
        create_upgrade_pr(
            from_ref=ocm.ComponentReference(
                name='foo', componentName='a.b/c1', version='1.2.3'
            ),
            to_ref=ocm.ComponentReference(
                name='bar', componentName='a.b/c1', version='2.0.0'
            ),
        )
    # error: mismatch in dependency types
    with pytest.raises(ValueError, match='reference types do not match'):
        create_upgrade_pr(
            from_ref=ocm.ComponentReference(
                name='dep_red', componentName='a.b/c1', version='1.2.3'
            ),
            to_ref=ocm.Resource(
                name='dep_red',
                version='2.0.0',
                type=ocm.ArtefactType.BLOB,
                access=None,
            ),
        )


def test_is_obsolete():
    examinee = create_upgrade_pr(
        from_ref=ocm.ComponentReference(
            name='c1',
            componentName='c1',
            version='1.2.3',
        ),
        to_ref=ocm.ComponentReference(
            name='c1',
            componentName='c1',
            version='2.0.0',
        ),
    )

    cref = ocm.ComponentReference(
        name='c1',
        componentName='c1',
        version='6.0.0',
    )

    reference_component = ocm.Component(
        name='c1',
        version='6.6.6',
        repositoryContexts=(),
        provider=None,
        sources=(),
        resources=(),
        componentReferences=()
    )

    # test with reference component not declaring this dependency
    assert not examinee.is_obsolete(reference_component=reference_component)

    # add differently-named dependency with greater version
    reference_component.componentReferences = (
        dataclasses.replace(cref, componentName='other-name'),
    )
    assert not examinee.is_obsolete(reference_component=reference_component)

    # add same-named web dependency with lesser version
    reference_component.componentReferences = (
        dataclasses.replace(cref, version='0.0.1'),
    )
    assert not examinee.is_obsolete(reference_component=reference_component)

    # add same-named resource of greater version but different type
    # todo: we should actually also test dependencies towards resources of two different types
    reference_component.resources = (
        ocm.Resource(
            name='c1',
            version='6.0.0',
            type=ocm.ArtefactType.BLOB,
            access=None,
        ),
    )
    assert not examinee.is_obsolete(reference_component=reference_component)

    # finally, add greater dependency of matching type and name
    reference_component.componentReferences = (
        dataclasses.replace(cref, version='9.9.9'),
    )
    assert examinee.is_obsolete(reference_component=reference_component)


def test_target_matches():
    old_resource = ocm.Resource(
        name='res1',
        version='1.2.3',
        type=ocm.ArtefactType.BLOB,
        access=ocm.Access(),
    )
    new_resource = ocm.Resource(
        name='res1',
        version='2.0.0',
        type=ocm.ArtefactType.BLOB,
        access=ocm.Access(),
    )

    examinee = create_upgrade_pr(
        from_ref=old_resource,
        to_ref=new_resource,
    )

    # test validation
    with pytest.raises(TypeError):
        examinee.target_matches(object()) # object is not of type DependencyBase

    # different type, same name and version
    assert not examinee.target_matches(
        ocm.Resource(
            name='res1',
            version='2.0.0',
            type=ocm.ArtefactType.OCI_IMAGE,
            access=None,
        )
    )

    # same type, and version, different name
    assert not examinee.target_matches(
        ocm.Resource(
            name='different-name',
            version='2.0.0',
            type=ocm.ArtefactType.BLOB,
            access=ocm.Access(),
        )
    )

    # same type, and name, different version
    assert not examinee.target_matches(
        ocm.Resource(
            name='res1',
            version='8.7.9',
            type=ocm.ArtefactType.BLOB,
            access=ocm.Access(),
        )
    )

    # all matches
    assert examinee.target_matches(
        ocm.Resource(
            name='res1',
            version='2.0.0',
            type=ocm.ArtefactType.BLOB,
            access=ocm.Access(),
        )
    )
