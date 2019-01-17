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

import enum
import os
import string
import shlex

import util

from concourse.model.base import (
    AttributeSpec,
    ModelBase,
    ModelValidationError,
    ScriptType,
    normalise_to_dict,
)


def attrs(pipeline_step):
    return (
        AttributeSpec.optional(
            name='depends',
            default=set(),
            doc='step names this step declares a dependency towards',
            type=set,
        ),
        AttributeSpec.optional(
            name='execute',
            default=pipeline_step.name,
            doc='''
            The executable (with optional additional arguments) to run. The executable path
            is calculated relative to `<main_repo>/.ci`.

            Has two forms:
            - scalar value (str in most cases) --> no shell-escaping is done
            - list of scalar values -> used verbatim as ARGV
            ''',
        ),
        AttributeSpec.optional(
            name='notifications_cfg',
            default='default',
            doc='''
            Configures build notification policies (see
            :ref:`notifications trait <trait-notifications>`)
            ''',
            type=str,
        ),
        AttributeSpec.optional(
            name='image',
            default=None,
            doc='''
            the container image reference to use for the executing container.
            If not set, the default image will be used.
            ''',
        ),
        AttributeSpec.optional(
            name='registry',
            default=None,
            doc='''
            The container image registry cfg_name. Required when retrieving container images
            from a non-default image registry that requires authentication.
            ''',
        ),
        AttributeSpec.optional(
            name='inputs',
            default={},
            doc='''
            a mapping of inputs produced by other build steps:
            { input_name: output_name }
            `input_name` is converted to UPPER_CASE and exposed to the step as an environment
            variable containing the relative path to the output.
            ''',
            type=dict,
        ),
        AttributeSpec.optional(
            name='output_dir',
            default=None,
            doc='''
            exposes a writable directory to the job. The directory is specified via environment
            variable named as the given value + _PATH (converted to UPPER-case and snake_case).
            Any files placed into this directory are passed to subsequent steps declaring the output
            as input. The unchanged value configured is used as input name.
            e.g.: `output_dir: out` results in env var `OUT_PATH`.
            ''',
        ),
        AttributeSpec.optional(
            name='publish_to',
            default={},
            doc='''
            has two forms:

            * list of logical repository names to which commits created by this step should be
              published.
            * a dictionary: <name: options>

            The second form currently accepts exactly one argument: `force_push` (bool) and is used
            to specify that a force-push should be done.

            Example:

            .. code-block:: yaml

                steps:
                    foo:
                        publish_to:
                            my_repo:
                                force_push: true
            ''',
            type=list,
        ),
        AttributeSpec.optional(
            name='vars',
            default={},
            doc='''
            pairs of {env_var_name: <python expression>}
            the specified python expressions are evaluated during pipeline replication.
            An instance of the current pipeline_model is accessible through the
            `pipeline_descriptor` symbol.
            The evaluation result is exposed to this build step via the specified environment
            variable.
            ''',
        ),
        AttributeSpec.optional(
            name='timeout',
            default=None,
            doc='''
            go-style time interval (e.g.: '1h30m') after which the step will be interrupted and fail.
            ''',
        ),
        AttributeSpec.optional(
            name='retries',
            default=None,
            doc='''
            positive integer specifying the maximum amount of failures until the step is
            counted as failed
            ''',
        ),
    )


class PipelineStep(ModelBase):
    def __init__(
        self,
        name,
        is_synthetic=False,
        script_type=ScriptType.BOURNE_SHELL,
        *args,
        **kwargs
    ):
        self.name = name
        self.is_synthetic = is_synthetic
        self._script_type = script_type
        self._outputs_dict = {}
        self._inputs_dict = {}
        self._publish_to_dict = {}
        super().__init__(*args, **kwargs)

    def _attribute_specs(self):
        return attrs(self)

    def _defaults_dict(self):
        return AttributeSpec.defaults_dict(attrs(self))

    def _optional_attributes(self):
        return set(AttributeSpec.optional_attr_names(attrs(self)))

    def custom_init(self, raw_dict: dict):
        raw_dict['depends'] = set(raw_dict['depends'])
        if raw_dict.get('output_dir', None):
            name = raw_dict['output_dir']
            self.add_output(name + '_path', name + '_path')

        # add hard-coded output "on_error" (allows build steps to pass custom
        # notification cfg for build errors)
        self.add_output('on_error_dir', 'on_error_dir')

        for name, variable_name in raw_dict.get('inputs').items():
            self.add_input(name, variable_name)

        # add hard-coded 'meta' input to every step if not already defined
        # TODO: remove existence check after new contract is propagated
        if 'meta' not in self.inputs().keys():
            self.add_input('meta', 'meta')

        self._publish_to_dict = normalise_to_dict(raw_dict['publish_to'])

    def script_type(self) -> ScriptType:
        '''
        returns the step's "script type"

        The script type specifies the execution environment in which the script payload is run
        (script payloads are hard-coded in pipeline templates).
        '''
        return self._script_type

    def notifications_cfg_name(self):
        return self.raw['notifications_cfg']

    def notifications_cfg(self):
        # injected by notifications trait
        return self._notifications_cfg

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
        outputs = self.outputs()
        if name not in outputs:
            raise ValueError(f'{name} not found in {list(outputs.keys())}')
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

    def timeout(self):
        return self.raw['timeout']

    def set_timeout(self, duration_string: str):
        util.not_empty(duration_string)
        self.raw['timeout'] = duration_string

    def retries(self):
        return self.raw['retries']

    def validate(self):
        super().validate()
        if self.image():
            image_reference = self.image()
            # image must be a valid docker image reference
            allowed_characters = string.ascii_letters + string.digits +'.-_/:'
            if any(map(lambda c: c not in allowed_characters, image_reference)):
                raise ModelValidationError(
                    'forbidden character in image reference: ' + str(image_reference)
                )
            if ':' not in image_reference:
                raise ModelValidationError(
                    'image reference must contain colon charater:' + str(image_reference)
                )

    def __str__(self):
        descr = 'PipelineStep {n} - depends: {d}, inputs: {i}, outputs: {o}'.format(
            n=self.name,
            d=self.depends(),
            i=self.inputs(),
            o=self.outputs(),
        )
        return descr
