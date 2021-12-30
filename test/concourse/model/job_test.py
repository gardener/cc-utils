import graphlib
import unittest

from concourse.model.base import ScriptType
from concourse.model.job import JobVariant

from concourse.model.step import PipelineStep


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
            script_type=ScriptType.BOURNE_SHELL,
            raw_dict=kwargs,
        )

    def test_step_ordering(self):
        examinee = self.examinee()
        examinee.add_step(self.pipeline_step(name='foo'))
        examinee.add_step(self.pipeline_step(name='bar', depends=['foo']))
        examinee.add_step(self.pipeline_step(name='baz', depends=['foo', 'bar']))

        ordered_steps = tuple(examinee.ordered_steps())

        assert len(ordered_steps) == 3

        first, second, third = ordered_steps

        assert len(first) == 1
        assert len(second) == 1
        assert len(third) == 1

        assert 'foo' in first
        assert 'bar' in second
        assert 'baz' in third

    def test_step_ordering_should_fail_on_circular_dependency(self):
        examinee = self.examinee()
        examinee.add_step(self.pipeline_step(name='foo', depends=['baz']))
        examinee.add_step(self.pipeline_step(name='bar', depends=['foo']))
        examinee.add_step(self.pipeline_step(name='baz', depends=['bar']))

        with self.assertRaises(graphlib.CycleError):
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

        ordered_steps = tuple(examinee.ordered_steps())

        for step_tuple in ordered_steps:
            assert len(step_tuple) == 1

        ordered_steps = tuple(step_tuple[0] for step_tuple in ordered_steps)

        assert ordered_steps == ('do_something', 'prepare', 'publish', 'foo')

        examinee = self.examinee()
        examinee.add_step(self.pipeline_step(name='do_something'))
        # TODO: Don't hardcode step names
        examinee.add_step(self.pipeline_step(
            name='prepare',
            is_synthetic=True,
            depends=['foo', 'bar', 'baz', 'do_something']
        ))
        examinee.add_step(self.pipeline_step(name='publish', is_synthetic=True, depends=['prepare']))

        examinee.add_step(self.pipeline_step(name='foo', depends=('publish',)))
        examinee.add_step(self.pipeline_step(name='bar', depends=('foo',)))
        examinee.add_step(self.pipeline_step(name='baz', depends=('bar',)))

        ordered_steps = tuple(examinee.ordered_steps())

        # first two steps can run in parallel, followed by sequential remainder
        first_two_steps = ordered_steps[0]
        remainder_steps = ordered_steps[1:]

        assert len(first_two_steps) == 2
        assert set(first_two_steps) == {'do_something', 'prepare'}

        for step_tuple in remainder_steps:
            assert len(step_tuple) == 1

        remainder_steps = tuple(step_tuple[0] for step_tuple in remainder_steps)

        assert remainder_steps == ('publish', 'foo', 'bar', 'baz')

        examinee = self.examinee()
        examinee.add_step(self.pipeline_step(name='do_something'))

        # TODO: Don't hardcode step names
        examinee.add_step(self.pipeline_step(
            name='prepare',
            is_synthetic=True,
            depends=['foo', 'bar', 'do_something']
        ))
        examinee.add_step(
            self.pipeline_step(name='publish', is_synthetic=True, depends=['prepare'])
        )

        examinee.add_step(self.pipeline_step(name='foo', depends=['publish']))
        examinee.add_step(self.pipeline_step(name='bar', depends=['publish']))

        ordered_steps = tuple(examinee.ordered_steps())
        assert len(ordered_steps) == 3 # (do_sth, prepare), (publish), (foo, bar)

        first_two_steps = ordered_steps[0]
        second_step = ordered_steps[1]
        last_two_steps = ordered_steps[2]

        assert len(first_two_steps) == 2
        assert len(second_step) == 1
        assert len(last_two_steps) == 2

        assert set(first_two_steps) == {'do_something', 'prepare'}
        assert set(second_step) == {'publish'}
        assert set(last_two_steps) == {'foo', 'bar'}

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

        with self.assertRaises(graphlib.CycleError):
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

        ordered_steps = tuple(examinee.ordered_steps())

        assert len(ordered_steps) == 2 # ('do_something', 'synthetic_foo'), then 'post_foo_step',

        first_two_steps, last_step = ordered_steps

        assert len(first_two_steps) == 2
        assert len(last_step) == 1

        assert set(first_two_steps) == {'do_something', 'synthetic_foo'}
        assert last_step[0] == 'post_foo_step'
