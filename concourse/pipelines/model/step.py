# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
# under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

import os
import string
import shlex

import util

from concourse.pipelines.modelbase import (
    ModelBase,
    ModelValidationError,
    ScriptType,
    normalise_to_dict,
)

class PipelineStep(ModelBase):
    def __init__(self, name, is_synthetic=False, script_type=ScriptType.BOURNE_SHELL, *args, **kwargs):
        self.name = name
        self.is_synthetic = is_synthetic
        self._script_type = script_type
        self._outputs_dict = {}
        self._inputs_dict = {}
        self._publish_to_dict = {}
        super().__init__(*args, **kwargs)

    def _defaults_dict(self):
        return {
            'depends': {self.name}, # toposort lib requires non-empty dependency sets
            'execute': self.name,
            'image': None,
            'inputs': {},
            'output_dir': None,
            'publish_to': {},
            'vars': {},
        }

    def custom_init(self, raw_dict: dict):
        raw_dict['depends'] = set(raw_dict['depends'])
        if raw_dict.get('output_dir', None):
            name = raw_dict['output_dir']
            self.add_output(name + '_path', name + '_path')

        for name, variable_name in raw_dict.get('inputs').items():
            self.add_input(name, variable_name)

        self._publish_to_dict = normalise_to_dict(raw_dict['publish_to'])

    def script_type(self) -> ScriptType:
        '''
        returns the step's "script type". The script type specifies the execution environment in which
        the script payload is run (script payloads are hard-coded in pipeline templates).
        '''
        return self._script_type

    def image(self):
        return self.raw['image']

    def _argv(self):
        execute = self.raw['execute']
        if not isinstance(execute, list):
            return [str(execute)]
        return [shlex.quote(str(e)) for e in execute]

    def executable(self, prefix=''):
        # by default, run an executable named as the step
        if isinstance(prefix, str):
            prefix = [prefix]
        return os.path.join(*prefix, self._argv()[0])

    def execute(self, prefix=''):
        argv = self._argv()
        argv[0] = self.executable(prefix=prefix)
        return ' '.join(argv)

    def registry(self):
        return self.raw.get('registry', None)

    def output_dir(self):
        if not self.raw['output_dir']:
            return None

        # an optional attribute specifying the "output directory"
        # due to "historical" reasons, append '-path' suffix
        return self.raw.get('output_dir') + '_path'

    def output(self, name):
        return self.outputs()[name]

    def outputs(self):
        return self._outputs_dict

    def add_output(self, name, variable_name):
        if name in self._outputs_dict:
            raise ValueError('output already exists: ' + str(name))
        self._outputs_dict[name] = variable_name

    def inputs(self):
        return self._inputs_dict

    def input(self, name):
        return self.inputs()[name]

    def add_input(self, name, variable_name):
        util.not_none(name)
        util.not_none(variable_name)

        if name in self._inputs_dict:
            raise ValueError('input already exists: ' + str(name))
        self._inputs_dict[name] = variable_name

    def variables(self):
        return self.raw.get('vars')

    def publish_repository_names(self):
        return self._publish_to_dict.keys()

    def publish_repository_dict(self):
        return self._publish_to_dict

    def _add_dependency(self, step: 'PipelineStep'):
        self.raw['depends'].add(step.name)

    def depends(self):
        return set(self.raw['depends'])

    def validate(self):
        super().validate()
        if self.image():
            image_reference = self.image()
            # image must be a valid docker image reference
            allowed_characters = string.ascii_letters + string.digits +'.-_/:'
            if not all(map(lambda c: c in allowed_characters, image_reference)):
                raise ModelValidationError('forbidden character in image reference: ' + str(image_reference))
            if not ':' in image_reference:
                raise ModelValidationError('image reference must contain colon charater:' + str(image_reference))

    def __str__(self):
        descr = 'PipelineStep {n} - depends: {d}, inputs: {i}, outputs: {o}'.format(
            n=self.name,
            d=self.depends(),
            i=self.inputs(),
            o=self.outputs(),
        )
        return descr
