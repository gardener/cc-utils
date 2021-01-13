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

import functools
import pytest
import unittest

import github.util as ghu
import product.model as pm

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
            type=cm.ResourceType.GENERIC,
            access=None,
        ),
        to_ref=cm.Resource(
            name='dep_red',
            version='2.0.0',
            type=cm.ResourceType.GENERIC,
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
                type=cm.ResourceType.GENERIC,
                access=None,
            ),
        )


class UpgradePullRequestTest(unittest.TestCase):

    def test_is_obsolete(self):
        examinee = create_upgrade_pr(
            from_ref=pm.WebDependencyReference.create(name='dep_red', version='1.2.3'),
            to_ref=pm.WebDependencyReference.create(name='dep_red', version='2.0.0'),
        )

        reference_component = pm.Component.create(
            name='a.b/ref/comp',
            version='6.6.6',
        )
        dependencies = reference_component.dependencies()

        # test with reference component not declaring this dependency
        self.assertFalse(examinee.is_obsolete(reference_component=reference_component))

        # add differently-named web dependency with greater version
        dependencies.add_web_dependency(
            pm.WebDependency.create(name='xxx', version='123', url='made-up.url')
        )
        self.assertFalse(examinee.is_obsolete(reference_component=reference_component))

        # add same-named web dependency with lesser version
        dependencies.add_web_dependency(
            pm.WebDependency.create(name='dep_red', version='0.0.1', url='made-up.url')
        )
        self.assertFalse(examinee.is_obsolete(reference_component=reference_component))

        # add same-named dependency of greater version but different type
        dependencies.add_generic_dependency(
            pm.GenericDependencyReference.create(name='dep_red', version='9.9.9')
        )
        self.assertFalse(examinee.is_obsolete(reference_component=reference_component))

        # finally, add greater dependency of matching type and name
        dependencies.add_web_dependency(
            pm.WebDependency.create(name='dep_red', version='9.9.9', url='made-up.url')
        )
        self.assertTrue(examinee.is_obsolete(reference_component=reference_component))

    def test_target_matches(self):
        old_resource = cm.Resource(
            name='res1',
            version='1.2.3',
            type=cm.ResourceType.GENERIC,
            access=cm.HttpAccess(
                url='made-up-url',
                type=cm.AccessType.HTTP,
            ),
        )
        new_resource = cm.Resource(
            name='res1',
            version='2.0.0',
            type=cm.ResourceType.GENERIC,
            access=cm.HttpAccess(
                url='made-up-url.2',
                type=cm.AccessType.HTTP,
            ),
        )

        examinee = create_upgrade_pr(
            from_ref=old_resource,
            to_ref=new_resource,
        )

        # test validation
        with self.assertRaises(TypeError):
            examinee.target_matches(object()) # object is not of type DependencyBase

        # different type, same name and version
        self.assertFalse(
            examinee.target_matches(
                pm.GenericDependencyReference.create(name='red', version='2.0.0')
            )
        )

        # same type, and version, different name
        self.assertFalse(
            examinee.target_matches(
                pm.WebDependency.create(name='xxx', version='2.0.0', url='made-up.url')
            )
        )

        # same type, and name, different version
        self.assertFalse(
            examinee.target_matches(
                pm.WebDependency.create(name='red', version='5.5.5', url='made-up.url')
            )
        )

        # all matches
        self.assertTrue(
            examinee.target_matches(
                pm.WebDependency.create(name='red', version='2.0.0', url='made-up.url')
            )
        )
