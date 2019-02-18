# Copyright (c) 2019 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
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

from util import not_none
from model import NamedModelElement

from concourse.model.step import PipelineStep
from concourse.model.base import (
  AttributeSpec,
  AttribSpecMixin,
  TraitTransformer,
  Trait,
  TraitTransformer,
  ModelValidationError,
  ModelDefaultsMixin,
  normalise_to_dict,
)


IMG_DESCRIPTOR_ATTRIBS = (
    AttributeSpec.optional(
        name='inputs',
        default={
            'repos': None, # None -> default to main repository
            'steps': {},
        },
        doc='configures the inputs that are made available to image build',
        type=dict, # todo: define types
    ),
    AttributeSpec.optional(
        name='tag_as_latest',
        default=False,
        doc='whether or not published container images should **also** be labeled as latest',
        type=bool,
    ),
    AttributeSpec.optional(
        name='dockerfile',
        default='Dockerfile',
        doc='the file to use for building the container image',
    ),
    AttributeSpec.optional(
        name='dir',
        default=None,
        doc='the relative path to the container image build file',
    ),
)


class PublishDockerImageDescriptor(NamedModelElement, ModelDefaultsMixin, AttribSpecMixin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_defaults(raw_dict=self.raw)

    def _attribute_specs(self):
        return IMG_DESCRIPTOR_ATTRIBS

    def _defaults_dict(self):
        return AttributeSpec.defaults_dict(IMG_DESCRIPTOR_ATTRIBS)

    def _optional_attributes(self):
        return set(AttributeSpec.optional_attr_names(IMG_DESCRIPTOR_ATTRIBS))

    def _inputs(self):
        return self.raw['inputs']

    def input_repos(self):
        return self._inputs()['repos']

    def input_steps(self):
        return self._inputs()['steps']

    def registry_name(self):
        return self.raw.get('registry')

    def image_reference(self):
        return self.raw['image']

    def tag_as_latest(self):
        return self.raw['tag_as_latest']

    def target_name(self):
        return self.raw.get('target_name')

    def dockerfile_relpath(self):
        return self.raw['dockerfile']

    def builddir_relpath(self):
        return self.raw['dir']

    def resource_name(self):
        parts = self.image_reference().split('/')
        # image references are lengthy (e.g. gcr.eu/<org>/<path>/../<name>)
        # -> shorten this a bit (keep domain and last part of url path)
        domain = parts[0]
        image_name = parts[-1]
        return '_'.join([domain, image_name])


ATTRIBUTES = (
    AttributeSpec.required(
        name='dockerimages',
        doc='specifies the container images to be built',
        type=PublishDockerImageDescriptor,
    ),
)


class PublishTrait(Trait):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _attribute_specs(self):
        return ATTRIBUTES

    def _defaults_dict(self):
        return AttributeSpec.defaults_dict(ATTRIBUTES)

    def _required_attributes(self):
        return set(AttributeSpec.required_attr_names(ATTRIBUTES))

    def dockerimages(self):
        return [
            PublishDockerImageDescriptor(name, args)
            for name, args
            in self.raw['dockerimages'].items()
        ]

    def transformer(self):
        return PublishTraitTransformer(trait=self)


class PublishTraitTransformer(TraitTransformer):
    name = 'publish'

    def __init__(self, trait: PublishTrait, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.trait = not_none(trait)

    def inject_steps(self):
        # 'publish' step
        publish_step = PipelineStep(name='publish', raw_dict={}, is_synthetic=True)
        publish_step.set_timeout(duration_string='4h')

        # 'prepare' step
        prepare_step = PipelineStep(name='prepare', raw_dict={}, is_synthetic=True)
        prepare_step.set_timeout(duration_string='30m')

        publish_step._add_dependency(prepare_step)

        yield prepare_step
        yield publish_step

    def process_pipeline_args(self, pipeline_args: 'JobVariant'):
        main_repo = pipeline_args.main_repository()
        prepare_step = pipeline_args.step('prepare')
        publish_step = pipeline_args.step('publish')

        image_name = main_repo.branch() + '-image'
        tag_name = main_repo.branch() + '-tag'

        # configure prepare step's outputs (consumed by publish step)
        prepare_step.add_output('image_path', image_name)
        prepare_step.add_output('tag_path', tag_name)

        # configure publish step's inputs (produced by prepare step)
        publish_step.add_input('image_path', image_name)
        publish_step.add_input('tag_path', tag_name)

        input_step_names = set()
        for image_descriptor in self.trait.dockerimages():
            # todo: image-specific prepare steps
            input_step_names.update(image_descriptor.input_steps())

        for input_step_name in input_step_names:
            input_step = pipeline_args.step(input_step_name)
            input_name = input_step.output_dir()
            prepare_step.add_input(input_name, input_name)

        # prepare-step depdends on every other step, except publish and release
        # TODO: do not hard-code knowledge about 'release' step
        for step in pipeline_args.steps():
            if step.name in ['publish', 'release']:
                continue
            prepare_step._add_dependency(step)
