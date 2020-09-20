# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

from concourse.model.base import (
    AttributeSpec,
    ModelBase,
)


class FilterCfg(ModelBase):
    @classmethod
    def _attribute_specs(cls):
        return (
            AttributeSpec.optional(
                name='include_image_references',
                default=(),
                doc='''
                a list of regular expressions. If configured, only matching image references are
                processed. By default, all image references are considered.
                ''',
            ),
            AttributeSpec.optional(
                name='exclude_image_references',
                default=(),
                doc='''
                a list of regular expressions. If configured, matching image references are
                exempted from processing. Has precedence over include_image_references.
                By default, no image references are excluded.
                ''',
            ),
            AttributeSpec.optional(
                name='include_image_names',
                default=(),
                doc='''
                a list of regular expressions. If configured, only matching image names are
                processed. By default, all image references are considered.
                ''',
            ),
            AttributeSpec.optional(
                name='exclude_image_names',
                default=(),
                doc='''
                a list of regular expressions. If configured, matching image names are
                exempted from processing. Has precedence over include_image_references.
                By default, no image references are excluded.
                ''',
            ),
            AttributeSpec.optional(
                name='include_component_names',
                default=(),
                doc='''
                a list of regular expressions. If configured, only image references from components
                whose name matches are considered.
                ''',
            ),
            AttributeSpec.optional(
                name='exclude_component_names',
                default=(),
                doc='''
                a list of regular expressions. If configured, image references from components whose
                name matches are excluded from further processing. Has precedence over
                include_component_names.
                ''',
            ),
        )

    def include_image_references(self):
        return self.raw['include_image_references']

    def exclude_image_references(self):
        return self.raw['exclude_image_references']

    def include_image_names(self):
        return self.raw['include_image_names']

    def exclude_image_names(self):
        return self.raw['exclude_image_names']

    def include_component_names(self):
        return self.raw['include_component_names']

    def exclude_component_names(self):
        return self.raw['exclude_component_names']


class ImageFilterMixin(ModelBase):
    def filters(self):
        return FilterCfg(raw_dict=self.raw['filters'])


IMAGE_ATTRS = (
    AttributeSpec.optional(
        name='filters',
        default={
            'include_image_references': (),
            'exclude_image_references': (),
            'include_image_names': (),
            'exclude_image_names': (),
            'include_component_names': (),
            'exclude_component_names': (),
        },
        doc='optional filters to restrict container images to process',
        type=FilterCfg,
    ),
)
