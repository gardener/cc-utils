# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import unittest

import shlex

from concourse.model.base import ScriptType
from concourse.model.step import PipelineStep


class PipelineStepTest(unittest.TestCase):
    def _examinee(self, name='dontcare',  **kwargs):
        return PipelineStep(
            name=name,
            is_synthetic=False,
            script_type=ScriptType.BOURNE_SHELL,
            raw_dict=kwargs,
        )

    def test_image(self):
        examinee = self._examinee(image='an_image:1.2.3')
        self.assertEqual(examinee.image(), 'an_image:1.2.3')

    def test__argv(self):
        # argv defaults to [step.name]
        examinee = self._examinee(name='a_name')
        self.assertEqual(examinee._argv(), ['a_name'])

        # executable may be overwritten
        examinee = self._examinee(execute='another_executable')
        self.assertEqual(examinee._argv(), ['another_executable'])

        # executable may be a list
        examinee = self._examinee(execute=['a', 'b'])
        self.assertEqual(examinee._argv(), ['a', 'b'])

    def test_executable(self):
        examinee = self._examinee(name='x')
        self.assertEqual(examinee.executable(), 'x')
        self.assertEqual(examinee.executable(prefix='foo'), 'foo/x')
        self.assertEqual(examinee.executable(prefix=('foo',)), 'foo/x')
        self.assertEqual(examinee.executable(prefix=('foo','bar')), 'foo/bar/x')

        examinee = self._examinee(execute='another_executable')
        self.assertEqual(examinee.executable(), 'another_executable')

        examinee = self._examinee(execute=['exec', 'arg 1', 'arg2'])
        self.assertEqual(examinee.executable(), 'exec')

    def test_execute(self):
        examinee = self._examinee(execute=['exec', 'arg1'])
        self.assertEqual(examinee.execute(), 'exec arg1')

        # whitespace must be quoted
        examinee = self._examinee(execute=['e x', 'a r g'])
        self.assertEqual(examinee.execute(), ' '.join(map(shlex.quote, ('e x', 'a r g'))))
