import unittest
import toposort

from concourse.model.base import ScriptType
from concourse.model.job import JobVariant

from concourse.model.step import (
    PipelineStep,
    StepNotificationPolicy,
)


class JobVariantTest(unittest.TestCase):
    def examinee(self, name='Dont care'):
        variant = JobVariant(
            name='Dont care', raw_dict={}, resource_registry={}
        )
        # set steps dict, usually done by factory.
        variant._steps_dict = {}
        return variant

    def pipeline_step(self, name, is_synthetic=False,  **kwargs):
        return PipelineStep(
            name=name,
            is_synthetic=is_synthetic,
            notification_policy=StepNotificationPolicy.NOTIFY_PULL_REQUESTS,
            script_type=ScriptType.BOURNE_SHELL,
            raw_dict=kwargs,
        )

    def test_step_ordering(self):
        examinee = self.examinee()
        examinee.add_step(self.pipeline_step(name='foo'))
        examinee.add_step(self.pipeline_step(name='bar', depends=['foo']))
        examinee.add_step(self.pipeline_step(name='baz', depends=['foo', 'bar']))

        ordered_steps = examinee.ordered_steps()

        self.assertListEqual(ordered_steps, [{'foo'}, {'bar'}, {'baz'}])

    def test_step_ordering_should_fail_on_circular_dependency(self):
        examinee = self.examinee()
        examinee.add_step(self.pipeline_step(name='foo', depends=['baz']))
        examinee.add_step(self.pipeline_step(name='bar', depends=['foo']))
        examinee.add_step(self.pipeline_step(name='baz', depends=['bar']))

        with self.assertRaises(toposort.CircularDependencyError):
            examinee.ordered_steps()

    def test_step_ordering_should_resolve_dependencies_on_publish_step(self):
        examinee = self.examinee()
        examinee.add_step(self.pipeline_step(name='do_something'))
        # TODO: Don't hardcode step names
        examinee.add_step(self.pipeline_step(
            name='prepare',
            is_synthetic=True,
            depends=['foo', 'do_something']
        ))
        examinee.add_step(self.pipeline_step(name='publish', is_synthetic=True, depends=['prepare']))

        examinee.add_step(self.pipeline_step(name='foo', depends=['publish']))

        ordered_steps = examinee.ordered_steps()

        self.assertListEqual(ordered_steps, [{'do_something'}, {'prepare'}, {'publish'}, {'foo'}])

        examinee = self.examinee()
        examinee.add_step(self.pipeline_step(name='do_something'))
        # TODO: Don't hardcode step names
        examinee.add_step(self.pipeline_step(
            name='prepare',
            is_synthetic=True,
            depends=['foo', 'bar', 'baz', 'do_something']
        ))
        examinee.add_step(self.pipeline_step(name='publish', is_synthetic=True, depends=['prepare']))

        examinee.add_step(self.pipeline_step(name='foo', depends=['publish']))
        examinee.add_step(self.pipeline_step(name='bar', depends=['foo']))
        examinee.add_step(self.pipeline_step(name='baz', depends=['bar']))

        ordered_steps = examinee.ordered_steps()
        self.assertListEqual(ordered_steps, [
            {'do_something'}, {'prepare'}, {'publish'}, {'foo'}, {'bar'}, {'baz'}
        ])

        examinee = self.examinee()
        examinee.add_step(self.pipeline_step(name='do_something'))
        # TODO: Don't hardcode step names
        examinee.add_step(self.pipeline_step(
            name='prepare',
            is_synthetic=True,
            depends=['foo', 'bar', 'do_something']
        ))
        examinee.add_step(self.pipeline_step(name='publish', is_synthetic=True, depends=['prepare']))

        examinee.add_step(self.pipeline_step(name='foo', depends=['publish']))
        examinee.add_step(self.pipeline_step(name='bar', depends=['publish']))

        ordered_steps = examinee.ordered_steps()
        self.assertListEqual(ordered_steps, [
            {'do_something'}, {'prepare'}, {'publish'}, {'foo','bar'}
        ])

    def test_step_ordering_should_fail_on_publish_step_if_synthetic_steps_in_cycle(self):
        examinee = self.examinee()
        examinee.add_step(self.pipeline_step(name='do_something'))
        # TODO: Don't hardcode step names
        examinee.add_step(self.pipeline_step(
            name='prepare',
            is_synthetic=True,
            depends=['foo', 'synthetic_foo'],
        ))
        examinee.add_step(self.pipeline_step(name='publish', is_synthetic=True, depends=['prepare']))

        examinee.add_step(self.pipeline_step(name='foo', depends=['publish']))
        examinee.add_step(self.pipeline_step(
            name='synthetic_foo',
            is_synthetic=True,
            depends=['publish'],
        ))

        with self.assertRaises(toposort.CircularDependencyError):
            examinee.ordered_steps()

    def test_step_ordering_should_resolve_cycles_with_synthetic_steps(self):
        examinee = self.examinee()
        examinee.add_step(self.pipeline_step(name='do_something'))
        examinee.add_step(self.pipeline_step(
            name='synthetic_foo',
            is_synthetic=True,
            depends=['do_something', 'post_foo_step']
        ))
        examinee.add_step(self.pipeline_step(name='post_foo_step', depends=['synthetic_foo']))

        ordered_steps = examinee.ordered_steps()
        self.assertListEqual(ordered_steps, [
            {'do_something'}, {'synthetic_foo'}, {'post_foo_step'}
        ])
