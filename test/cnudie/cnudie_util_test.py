# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import pytest

import cnudie.util
import ocm

# functions under test
diff_components = cnudie.util.diff_components
diff_resources = cnudie.util.diff_resources


@pytest.fixture
def cid():
    def component_id(name: str, version: str):
        return ocm.ComponentIdentity(name=name, version=version)

    return component_id


def comp(
    name,
    version,
    componentReferences=None,
) -> ocm.Component:
    return ocm.Component(
        name=name,
        version=version,
        provider={
            'name': 'some company',
        },
        repositoryContexts=[],
        componentReferences=componentReferences or [],
        sources=[],
        resources=[],
        labels=[],
    )


def comp_desc(name, version) -> ocm.ComponentDescriptor:
    return ocm.ComponentDescriptor(
        meta=ocm.Metadata(),
        component=comp(name, version),
    )


@pytest.fixture
def iref():
    def image_ref(name, version):
        return ocm.Resource(
            name=name,
            version=version,
            access=ocm.OciAccess,
            type=ocm.AccessType.OCI_REGISTRY,
            extraIdentity={},
            labels=[],
            srcRefs=[],
            relation=None,
        )

    return image_ref


def test_componentdiff_to_str(cid):
    diff = cnudie.util.ComponentDiff(
        cidentities_only_left=(cid(name='a', version='1.0.0'), cid(name='b', version='2.0.0')),
        cidentities_only_right=(cid(name='c', version='3.0.0'),),  # added
        cpairs_version_changed=[
            (comp('d', '1.0.0'), comp('d', '2.0.0'))  # changed
        ],
        names_only_left={'a', 'b'},
        names_only_right={'c'},
        names_version_changed={'d'},
    )
    result = cnudie.util.format_component_diff(
        component_diff=diff,
        delivery_dashboard_url_view_diff=None,
        delivery_dashboard_url=None,
    )

    max_length = 10
    if len(result) > max_length:
        truncated_result = result[:result.find('## Component Details:')]
        truncated_result += '\n... [Component details omitted]\n'
        body = truncated_result
    else:
        body = result

    assert '### Added Components:' in body
    assert '... [Component details omitted]' in body
    assert '## Component Details:' not in body


def test_iter_sorted():
    def cref(component: ocm.Component):
        return ocm.ComponentReference(
            name='dont-care',
            componentName=component.name,
            version=component.version,
            extraIdentity={},
        )

    comp_a = comp(name='a', version=1)
    comp_b = comp(name='b', version=1, componentReferences=[cref(comp_a)])
    comp_c = comp(name='c', version=1, componentReferences=[cref(comp_a), cref(comp_b)])

    sorted_comps = tuple(cnudie.util.iter_sorted((comp_c, comp_b, comp_a)))

    assert sorted_comps == (comp_a, comp_b, comp_c)


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
        ocm.Source(
            name='repo_aux_source',
            access=ocm.GithubAccess(
                type=ocm.AccessType.GITHUB,
                ref='refs/heads/master',
                repoUrl='github.com/otherOrg/otherRepo'
            ),
            labels=[
                ocm.Label(
                    name='cloud.gardener/cicd/source',
                    value={'repository-classification': 'auxiliary'},
                ),
            ],
        ),
        ocm.Source(
            name='repo_main_source',
            access=ocm.GithubAccess(
                type=ocm.AccessType.GITHUB,
                ref='refs/heads/master',
                repoUrl='github.com/org/repo'
            ),
            labels=[
                ocm.Label(
                    name='cloud.gardener/cicd/source',
                    value={'repository-classification': 'main'},
                ),
            ],
        ),
    ]
    component_with_source_label = ocm.Component(
        name=component_name,
        version=component_version,
        sources=sources,
        componentReferences=[],
        labels=[],
        repositoryContexts=[
            ocm.OciOcmRepository(
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

    component_without_source_label = ocm.Component(
        name=component_name,
        version=component_version,
        sources=[
            ocm.Source(
                name='repo_main_source',
                access=ocm.GithubAccess(
                    type=ocm.AccessType.GITHUB,
                    ref='refs/heads/master',
                    repoUrl='github.com/org/repo'
                ),
            ),
            ocm.Source(
                name='repo_aux_source',
                access=ocm.GithubAccess(
                    type=ocm.AccessType.GITHUB,
                    ref='refs/heads/master',
                    repoUrl='github.com/otherOrg/otherRepo'
                ),
            ),
        ],
        componentReferences=[],
        labels=[],
        repositoryContexts=[
            ocm.OciOcmRepository(
                baseUrl='eu.gcr.io/sap-se-gcr-k8s-private/cnudie/gardener/landscapes',
                type='ociRegistry',
            ),
        ],
        resources=[],
        provider=[],
    )

    main_source = cnudie.util.determine_main_source_for_component(component_without_source_label)

    assert main_source.name == 'repo_main_source'


def test_diff_label():
    label_foo = ocm.Label(name='foo', value='bar v1')

    left_labels = [
        label_foo
    ]
    right_labels = [
        label_foo
    ]

    # check identical label in both lists
    label_diff = cnudie.util.diff_labels(left_labels=left_labels, right_labels=right_labels)
    assert len(label_diff.label_pairs_changed) == 0
    assert len(label_diff.labels_only_left) == 0
    assert len(label_diff.labels_only_right) == 0

    # check left exclusive label
    label_only_left = ocm.Label(name='left', value='only')
    left_labels.append(label_only_left)
    label_diff = cnudie.util.diff_labels(left_labels=left_labels, right_labels=right_labels)
    assert len(label_diff.label_pairs_changed) == 0
    assert len(label_diff.labels_only_left) == 1
    assert label_diff.labels_only_left[0] == label_only_left
    assert len(label_diff.labels_only_right) == 0

    # check right exclusive label
    label_only_right = ocm.Label(name='right', value='only')
    right_labels.append(label_only_right)
    label_diff = cnudie.util.diff_labels(left_labels=left_labels, right_labels=right_labels)
    assert len(label_diff.label_pairs_changed) == 0
    assert len(label_diff.labels_only_left) == 1
    assert len(label_diff.labels_only_right) == 1
    assert label_diff.labels_only_right[0] == label_only_right

    # check removal of one label
    right_labels.remove(label_foo)
    label_diff = cnudie.util.diff_labels(left_labels=left_labels, right_labels=right_labels)
    assert len(label_diff.label_pairs_changed) == 0
    assert len(label_diff.labels_only_left) == 2
    assert label_diff.labels_only_left[0] == label_foo
    assert len(label_diff.labels_only_right) == 1

    # check different label value with the same name
    label_foo_updated = ocm.Label(name='foo', value='bar v2')
    right_labels.append(label_foo_updated)
    label_diff = cnudie.util.diff_labels(left_labels=left_labels, right_labels=right_labels)
    assert len(label_diff.label_pairs_changed) == 1
    assert label_diff.label_pairs_changed[0] == (label_foo, label_foo_updated)
    assert len(label_diff.labels_only_left) == 1
    assert len(label_diff.labels_only_right) == 1

    # check that duplicate label name in one list cause exception
    right_labels.append(label_foo_updated)
    with pytest.raises(RuntimeError) as re:
        label_diff = cnudie.util.diff_labels(left_labels=left_labels, right_labels=right_labels)
    assert re != None


def test_to_component_id():
    base_identity = ocm.ComponentIdentity(name='Foo', version='1.2.3')

    test_identity = ocm.ComponentIdentity(name='Foo', version='1.2.3')
    assert cnudie.util.to_component_id(test_identity) == base_identity

    test_component = ocm.Component(
        name='Foo',
        version='1.2.3',
        repositoryContexts=[],
        provider='',
        sources=[],
        componentReferences=[],
        resources=[],
    )
    assert cnudie.util.to_component_id(test_component) == base_identity

    test_component_descriptor = ocm.ComponentDescriptor(
        meta=ocm.Metadata(),
        component=test_component,
        signatures=[],
    )
    assert cnudie.util.to_component_id(test_component_descriptor) == base_identity

    test_component_reference = ocm.ComponentReference(
        componentName='Foo', name='Bar', version='1.2.3',
    )
    assert cnudie.util.to_component_id(test_component_reference) == base_identity

    test_str = 'Foo:1.2.3'
    assert cnudie.util.to_component_id(test_str) == base_identity

    test_str = 'Foo'
    with pytest.raises(ValueError):
        cnudie.util.to_component_id(test_str)

    test_tuple = 'Foo', '1.2.3'
    assert cnudie.util.to_component_id(test_tuple) == base_identity


def test_to_component_name():
    base_name = 'Foo'

    test_identity = ocm.ComponentIdentity(name='Foo', version='1.2.3')
    assert cnudie.util.to_component_name(test_identity) == base_name

    test_component = ocm.Component(
        name='Foo',
        version='1.2.3',
        repositoryContexts=[],
        provider='',
        sources=[],
        componentReferences=[],
        resources=[],
    )
    assert cnudie.util.to_component_name(test_component) == base_name

    test_component_descriptor = ocm.ComponentDescriptor(
        meta=ocm.Metadata(),
        component=test_component,
        signatures=[],
    )
    assert cnudie.util.to_component_name(test_component_descriptor) == base_name

    test_component_reference = ocm.ComponentReference(
        componentName='Foo', name='Bar', version='1.2.3',
    )
    assert cnudie.util.to_component_name(test_component_reference) == base_name

    test_str = 'Foo:1.2.3'
    assert cnudie.util.to_component_name(test_str) == base_name

    test_str = 'Foo:Bar:Baz'
    with pytest.raises(ValueError):
        cnudie.util.to_component_name(test_str)

    test_tuple = 'Foo', '1.2.3'
    assert cnudie.util.to_component_name(test_tuple) == base_name
