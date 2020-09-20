# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import enum

from concourse.model.job import (
    JobVariant,
)
from concourse.model.step import (
    PipelineStep,
    StepNotificationPolicy,
)
from concourse.model.base import (
  AttributeSpec,
  Trait,
  TraitTransformer,
  ScriptType,
)


class VersionInterface(enum.Enum):
    FILE = 'file'
    CALLBACK = 'callback'


ATTRIBUTES = (
    AttributeSpec.optional(
        name='preprocess',
        default='inject-commit-hash',
        doc='sets the semver version operation to calculate the effective version during the build',
    ),
    AttributeSpec.optional(
        name='versionfile',
        default='VERSION',
        doc='relative path to the version file',
    ),
    AttributeSpec.optional(
        name='inject_effective_version',
        default=False,
        doc='''
        whether or not the effective version is to be written into the source tree's VERSION file
        ''',
        type=bool,
    ),
    AttributeSpec.optional(
        name='version_interface',
        default=VersionInterface.FILE,
        doc='how the version can be read/written',
        type=VersionInterface,
    ),
    AttributeSpec.optional(
        name='read_callback',
        default=None,
        doc='relative path to an executable that returns current version via stdout',
    ),
    AttributeSpec.optional(
        name='write_callback',
        default=None,
        doc='relative path to an executable that accepts version from stdin and writes it',
    ),
)


class VersionTrait(Trait):
    PREPROCESS_OPS = {
        'finalise',
        'finalize',
        'inject-branch-name',
        'inject-commit-hash',
        'noop',
        'use-branch-name',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if not self._preprocess() in self.PREPROCESS_OPS:
            raise ValueError('preprocess must be one of: ' + ', '.join(self.PREPROCESS_OPS))

    @classmethod
    def _attribute_specs(cls):
        return ATTRIBUTES

    def _preprocess(self):
        return self.raw['preprocess']

    def versionfile_relpath(self):
        return self.raw['versionfile']

    def inject_effective_version(self):
        return self.raw['inject_effective_version']

    def version_interface(self):
        return VersionInterface(self.raw.get('version_interface'))

    def read_callback(self):
        return self.raw.get('read_callback')

    def write_callback(self):
        return self.raw.get('write_callback')

    @classmethod
    def transformer(self):
        return VersionTraitTransformer()


ENV_VAR_NAME = 'version_path'
DIR_NAME = 'managed-version'


class VersionTraitTransformer(TraitTransformer):
    name = 'version'

    def inject_steps(self):
        self.version_step = PipelineStep(
            name='version',
            raw_dict={},
            is_synthetic=True,
            notification_policy=StepNotificationPolicy.NO_NOTIFICATION,
            script_type=ScriptType.PYTHON3,
            )
        self.version_step.add_output(name=DIR_NAME, variable_name=ENV_VAR_NAME)
        self.version_step.set_timeout(duration_string='5m')

        yield self.version_step

    def process_pipeline_args(self, pipeline_args: JobVariant):
        # all steps depend from us and may consume our output
        for step in pipeline_args.steps():
            if step == self.version_step:
                continue
            step._add_dependency(self.version_step)
            step.add_input(variable_name=ENV_VAR_NAME, name=DIR_NAME)
