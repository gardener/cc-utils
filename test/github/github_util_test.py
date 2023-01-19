# Copyright (c) 2019-2020 SAP SE or an SAP affiliate company. All rights reserved. This file is
# licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import dataclasses
import functools
import pytest

import github.util as ghu

import gci.componentmodel as cm


# test gear
pull_request_mock = object() # keep this as simple as possible for now
create_upgrade_pr = functools.partial(
    ghu.UpgradePullRequest,
    pull_request=pull_request_mock,
)


def test_ctor():
    # upgrade component
    create_upgrade_pr(
        from_ref=cm.ComponentReference(
            name='abcd', componentName='a.b/c1', version='1.2.3'
        ),
        to_ref=cm.ComponentReference(
            name='abcd', componentName='a.b/c1', version='2.0.0'
        ),
    )
    # upgrade web dependency
    create_upgrade_pr(
        from_ref=cm.Resource(
            name='dep_red',
            version='1.2.3',
            type=cm.ArtefactType.BLOB,
            access=None,
        ),
        to_ref=cm.Resource(
            name='dep_red',
            version='2.0.0',
            type=cm.ArtefactType.BLOB,
            access=None,
        ),
    )
    # error: mismatch in dependency name
    with pytest.raises(ValueError, match='reference name mismatch'):
        create_upgrade_pr(
            from_ref=cm.ComponentReference(
                name='foo', componentName='a.b/c1', version='1.2.3'
            ),
            to_ref=cm.ComponentReference(
                name='bar', componentName='a.b/c1', version='2.0.0'
            ),
        )
    # error: mismatch in dependency types
    with pytest.raises(ValueError, match='reference types do not match'):
        create_upgrade_pr(
            from_ref=cm.ComponentReference(
                name='dep_red', componentName='a.b/c1', version='1.2.3'
            ),
            to_ref=cm.Resource(
                name='dep_red',
                version='2.0.0',
                type=cm.ArtefactType.BLOB,
                access=None,
            ),
        )


def test_is_obsolete():
    examinee = create_upgrade_pr(
        from_ref=cm.ComponentReference(
            name='c1',
            componentName='c1',
            version='1.2.3',
        ),
        to_ref=cm.ComponentReference(
            name='c1',
            componentName='c1',
            version='2.0.0',
        ),
    )

    cref = cm.ComponentReference(
        name='c1',
        componentName='c1',
        version='6.0.0',
    )

    reference_component = cm.Component(
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
        cm.Resource(
            name='c1',
            version='6.0.0',
            type=cm.ArtefactType.BLOB,
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
    old_resource = cm.Resource(
        name='res1',
        version='1.2.3',
        type=cm.ArtefactType.BLOB,
        access=cm.ResourceAccess(),
    )
    new_resource = cm.Resource(
        name='res1',
        version='2.0.0',
        type=cm.ArtefactType.BLOB,
        access=cm.ResourceAccess(),
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
        cm.Resource(
            name='res1',
            version='2.0.0',
            type=cm.ResourceType.OCI_IMAGE,
            access=None,
        )
    )

    # same type, and version, different name
    assert not examinee.target_matches(
        cm.Resource(
            name='different-name',
            version='2.0.0',
            type=cm.ArtefactType.BLOB,
            access=cm.ResourceAccess(),
        )
    )

    # same type, and name, different version
    assert not examinee.target_matches(
        cm.Resource(
            name='res1',
            version='8.7.9',
            type=cm.ArtefactType.BLOB,
            access=cm.ResourceAccess(),
        )
    )

    # all matches
    assert examinee.target_matches(
        cm.Resource(
            name='res1',
            version='2.0.0',
            type=cm.ArtefactType.BLOB,
            access=cm.ResourceAccess(),
        )
    )
