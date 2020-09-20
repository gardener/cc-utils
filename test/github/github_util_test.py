# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import functools
import unittest

import github.util as ghu
import product.model as pm


# test gear
pull_request_mock = object() # keep this as simple as possible for now
create_upgrade_pr = functools.partial(
    ghu.UpgradePullRequest,
    pull_request=pull_request_mock,
)


class UpgradePullRequestTest(unittest.TestCase):
    def test_ctor(self):
        # upgrade component
        create_upgrade_pr(
            from_ref=pm.ComponentReference.create(name='a.b/c/d', version='1.2.3'),
            to_ref=pm.ComponentReference.create(name='a.b/c/d', version='2.0.0'),
        )
        # same, but use Component + ComponentReference
        create_upgrade_pr(
            from_ref=pm.Component.create(name='a.b/c/d', version='1.2.3'),
            to_ref=pm.ComponentReference.create(name='a.b/c/d', version='2.0.0'),
        )
        # upgrade web dependency
        create_upgrade_pr(
            from_ref=pm.WebDependencyReference.create(name='dep_red', version='1.2.3'),
            to_ref=pm.WebDependencyReference.create(name='dep_red', version='2.0.0'),
        )
        # error: mismatch in dependency name
        with self.assertRaisesRegex(ValueError, 'names do not match'):
            create_upgrade_pr(
                from_ref=pm.GenericDependencyReference.create(name='foo', version='1.2.3'),
                to_ref=pm.GenericDependencyReference.create(name='bar', version='1.2.3'),
            )
        # error: mismatch in dependency types
        with self.assertRaisesRegex(ValueError, 'type names do not match'):
            create_upgrade_pr(
                from_ref=pm.GenericDependencyReference.create(name='foo', version='1.2.3'),
                to_ref=pm.WebDependencyReference.create(name='foo', version='1.2.3'),
            )

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
        examinee = create_upgrade_pr(
            from_ref=pm.WebDependency.create(name='red', version='1.2.3', url='made-up.url'),
            to_ref=pm.WebDependency.create(name='red', version='2.0.0', url='made-up.url'),
        )

        # test validation
        with self.assertRaises(ValueError):
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
