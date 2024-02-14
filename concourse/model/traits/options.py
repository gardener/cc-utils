# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


from ci.util import not_none
from concourse.model.base import (
    AttributeSpec,
    Trait,
    TraitTransformer
)
from concourse.model.job import (
    JobVariant,
)


ATTRIBUTES = (
    AttributeSpec.optional(
        name='build_logs_to_retain',
        default=1000,
        doc='the amount of build logs to retain before log rotation occurs',
        type=int,
    ),
    AttributeSpec.optional(
        name='public_build_logs',
        default=False,
        doc='whether or not build logs are accessible to unauthenticated users',
        type=bool,
    ),
)


class OptionsTrait(Trait):
    @classmethod
    def _attribute_specs(cls):
        return ATTRIBUTES

    def build_logs_to_retain(self):
        return self.raw['build_logs_to_retain']

    def public_build_logs(self):
        return self.raw['public_build_logs']

    def transformer(self):
        return OptionsTraitTransformer(trait=self)


class OptionsTraitTransformer(TraitTransformer):
    name = 'options'

    def __init__(self, trait: OptionsTrait, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.trait = not_none(trait)

    def process_pipeline_args(self, pipeline_args: JobVariant):
        pass
