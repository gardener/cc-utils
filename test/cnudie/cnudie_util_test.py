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

import pytest
import cnudie.util

import gci.componentmodel as cm

# functions under test
diff_components = cnudie.util.diff_components
diff_resources = cnudie.util.diff_resources


def component_id_v2(name: str, version: str):
    return cm.ComponentIdentity(name=name, version=version)


@pytest.fixture
def cid():
    return component_id_v2


def comp(name, version) -> cm.Component:
    return cm.Component(name, version, [],[],[],[],[],[])


def image_ref(name, version):
    return cm.Resource(
        name=name,
        version=version,
        access=cm.OciAccess,
        type=cm.AccessType.OCI_REGISTRY,
        extraIdentity={},
        labels=[],
        srcRefs=[],
        relation=None,
    )


@pytest.fixture
def iref():
    return image_ref


def test_remove_component(cid):
    left_components = [
        comp('c1', '1.2.3'),
        comp('c2', 'v2.0.0'),
    ]

    right_components = [
        comp('c1', '1.2.3'), # version changed
        # comp('c2', '1.2.3'), # removed
    ]

    result = diff_components(left_components=left_components, right_components=right_components)
    assert result.cidentities_only_left ==  {cid('c2', 'v2.0.0')}
    assert result.cidentities_only_right == set()
    assert result.cpairs_version_changed == []
    assert result.names_only_left == {'c2'}
    assert result.names_only_right == set()
    assert result.names_version_changed == set()

    right_components.append(comp('c2', 'v2.0.0'))
    left_components.append(comp('c2', 'v1.4.0'))
    left_components.append(comp('c2', 'v1.5.0'))

    result = diff_components(left_components=left_components, right_components=right_components)
    assert result.cidentities_only_left ==  {
        cid('c2', 'v1.4.0'),
        cid('c2', 'v1.5.0'),
    }
    assert result.cidentities_only_right == set()
    assert result.cpairs_version_changed == []
    assert result.names_only_left == set()
    assert result.names_only_right == set()
    assert result.names_version_changed == set()


def test_diff_components(cid):
    left_components = (
        comp('c1', '1.2.3'),
        comp('c2', '1.2.3'),
        comp('c3', '1.2.3'),
        # comp('c4', '1.2.3'), # missing on left
        comp('c5', '1.2.3'), # version changed
    )

    right_components = (
        comp('c1', '2.2.3'), # version changed
        comp('c2', '1.2.3'), # no change
        #cid('c3', '1.2.3'), # missing on right
        comp('c4', '1.2.3'), # added on right
        comp('c5', '2.3.4'), # version changed
    )

    result = diff_components(left_components, right_components)

    assert result.cidentities_only_left == {
        cid('c1', '1.2.3'), cid('c3', '1.2.3'), cid('c5', '1.2.3'),
    }
    assert result.cidentities_only_right == {
        cid('c1', '2.2.3'), cid('c4', '1.2.3'), cid('c5', '2.3.4'),
    }
    assert result.cpairs_version_changed == [
        (comp('c1', '1.2.3'), comp('c1', '2.2.3')),
        (comp('c5', '1.2.3'), comp('c5', '2.3.4')),
    ]
    assert result.names_only_left == {'c3'}
    assert result.names_only_right == {'c4'}
    assert result.names_version_changed == {'c1','c5'}


#TODO add other resources than OCI images
def test_diff_resources(iref):
    left_comp = comp('x.o/a/b', '1.2.3')
    right_comp = comp('x.o/a/b', '2.3.4')

    img1 = iref('i1', '1.2.3')

    left_comp.resources.append(img1)
    right_comp.resources.append(img1)

    img_diff = diff_resources(left_component=left_comp, right_component=right_comp)

    # same image added declared by left and right - expect empty diff
    assert img_diff.left_component == left_comp
    assert img_diff.right_component == right_comp
    assert len(img_diff.resource_refs_only_right) == 0
    assert len(img_diff.resource_refs_only_left) == 0

    img2 = iref('i2', '1.2.3')
    img3 = iref('i3', '1.2.3')
    left_comp.resources.append(img2)
    right_comp.resources.append(img3)

    # img2 only left, img3 only right
    resource_diff = diff_resources(left_component=left_comp, right_component=right_comp)
    assert len(resource_diff.resource_refs_only_left) == 1
    assert len(resource_diff.resource_refs_only_right) == 1
    assert list(resource_diff.resource_refs_only_left)[0] == img2
    assert list(resource_diff.resource_refs_only_right)[0] == img3

    img4_0 = iref('i4', '1.2.3')
    img4_1 = iref('i4', '2.0.0') # changed version
    left_comp.resources.append(img4_0)
    right_comp.resources.append(img4_1)
    resource_diff = diff_resources(left_component=left_comp, right_component=right_comp)
    assert len(resource_diff.resource_refs_only_left) == 1
    assert len(resource_diff.resource_refs_only_right) == 1
    assert len(resource_diff.resourcepairs_version_changed) == 1
    left_i, right_i = list(resource_diff.resourcepairs_version_changed)[0]
    assert type(left_i) == type(img4_0)
    assert left_i == img4_0
    assert right_i == img4_1

    # test whether exclusive images with the same name are working
    img5_0 = iref('res5', '1.2.3')
    img5_1 = iref('res5', '1.2.4')
    img5_2 = iref('res5', '1.2.5')
    left_comp.resources.append(img5_0)
    left_comp.resources.append(img5_1)
    left_comp.resources.append(img5_2)
    resource_diff = diff_resources(left_component=left_comp, right_component=right_comp)
    assert len(resource_diff.resource_refs_only_left) == 4
    assert len(resource_diff.resource_refs_only_right) == 1
    assert list(resource_diff.resource_refs_only_left)[1] == img5_0
    assert list(resource_diff.resource_refs_only_left)[2] == img5_1
    assert list(resource_diff.resource_refs_only_left)[3] == img5_2

    # test if grouping semantic does work
    right_comp.resources.append(img5_0)
    img5_3 = iref('res5', '1.2.6')
    right_comp.resources.append(img5_3)
    resource_diff = diff_resources(left_component=left_comp, right_component=right_comp)

    assert len(resource_diff.resource_refs_only_left) == 2
    assert len(resource_diff.resource_refs_only_right) == 1
    assert len(resource_diff.resourcepairs_version_changed) == 2


def test_label_usage():
    component_name = 'c'
    component_version = '1.2.3'
    sources = [
        cm.ComponentSource(
            name='repo_aux_source',
            access=cm.GithubAccess(
                type=cm.AccessType.GITHUB,
                ref='refs/heads/master',
                repoUrl='github.com/otherOrg/otherRepo'
            ),
            labels=[
                cm.Label(
                    name='cloud.gardener/cicd/source',
                    value={'repository-classification': 'auxiliary'},
                ),
            ],
        ),
        cm.ComponentSource(
            name='repo_main_source',
            access=cm.GithubAccess(
                type=cm.AccessType.GITHUB,
                ref='refs/heads/master',
                repoUrl='github.com/org/repo'
            ),
            labels=[
                cm.Label(
                    name='cloud.gardener/cicd/source',
                    value={'repository-classification': 'main'},
                ),
            ],
        ),
    ]
    component_with_source_label = cm.Component(
        name=component_name,
        version=component_version,
        sources=sources,
        componentReferences=[],
        labels=[],
        repositoryContexts=[
            cm.RepositoryContext(
                baseUrl='eu.gcr.io/sap-se-gcr-k8s-private/cnudie/gardener/landscapes',
                type='ociRegistry',
            ),
        ],
        resources=[],
        provider=[],
    )

    main_source = cnudie.util.determine_main_source_for_component(component_with_source_label,)
    assert main_source.labels[0].value == {'repository-classification': 'main'}
    assert main_source.name == 'repo_main_source'

    component_without_source_label = cm.Component(
        name=component_name,
        version=component_version,
        sources=[
            cm.ComponentSource(
                name='repo_main_source',
                access=cm.GithubAccess(
                    type=cm.AccessType.GITHUB,
                    ref='refs/heads/master',
                    repoUrl='github.com/org/repo'
                ),
            ),
            cm.ComponentSource(
                name='repo_aux_source',
                access=cm.GithubAccess(
                    type=cm.AccessType.GITHUB,
                    ref='refs/heads/master',
                    repoUrl='github.com/otherOrg/otherRepo'
                ),
            ),
        ],
        componentReferences=[],
        labels=[],
        repositoryContexts=[
            cm.RepositoryContext(
                baseUrl='eu.gcr.io/sap-se-gcr-k8s-private/cnudie/gardener/landscapes',
                type='ociRegistry',
            ),
        ],
        resources=[],
        provider=[],
    )

    main_source = cnudie.util.determine_main_source_for_component(component_without_source_label)

    assert main_source.name == 'repo_main_source'
