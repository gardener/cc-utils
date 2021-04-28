# Copyright (c) 2019-2020 SAP SE or an SAP affiliate company. All rights reserved. This file is
# licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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
import typing

import dacite

from ci.util import not_none
from model import NamedModelElement
import concourse.paths
import gci.componentmodel as cm

from concourse.model.job import (
    JobVariant,
)
from concourse.model.step import (
    PipelineStep,
    StepNotificationPolicy,
)
from concourse.model.base import (
  AttributeSpec,
  AttribSpecMixin,
  ScriptType,
  Trait,
  TraitTransformer,
)
from model.base import (
  ModelDefaultsMixin,
  ModelValidationError,
)


IMG_DESCRIPTOR_ATTRIBS = (
    AttributeSpec.optional(
        name='registry',
        default=None,
        type=str,
        doc='name of the registry config to use when pushing the image (see cc-utils).',
    ),
    AttributeSpec.required(
        name='image',
        type=str,
        doc='image reference to publish the created container image to.',
    ),
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
        name='tag_template',
        default='${EFFECTIVE_VERSION}',
        doc='the template to use for the image-tag (only variable: EFFECTIVE_VERSION)',
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
    AttributeSpec.optional(
        name='target',
        default=None,
        doc='only for multistage builds: the target up to which to build',
    ),
    AttributeSpec.optional(
        name='resource_labels',
        default=[],
        type=typing.List[cm.Label],
        doc='labels to add to the resource declaration for this image in base-component-descriptor'
    ),
    AttributeSpec.optional(
        name='build_args',
        default={},
        type=typing.Dict[str, str],
        doc='build-time arguments to pass to docker-build',
    ),
)


class PublishDockerImageDescriptor(NamedModelElement, ModelDefaultsMixin, AttribSpecMixin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not isinstance(self.raw, dict):
            raise ModelValidationError(
                f'{self.__class__.__name__} expects a dict - got: {self.raw=}'
            )
        self._apply_defaults(raw_dict=self.raw)

    @classmethod
    def _attribute_specs(cls):
        return IMG_DESCRIPTOR_ATTRIBS

    def _defaults_dict(self):
        return AttributeSpec.defaults_dict(IMG_DESCRIPTOR_ATTRIBS)

    def _optional_attributes(self):
        return set(AttributeSpec.optional_attr_names(IMG_DESCRIPTOR_ATTRIBS))

    def _required_attributes(self):
        return set(AttributeSpec.required_attr_names(IMG_DESCRIPTOR_ATTRIBS))

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

    def tag_as_latest(self) -> bool:
        return self.raw['tag_as_latest']

    def additional_tags(self) -> typing.Tuple[str]:
        if self.tag_as_latest():
            return ('latest',)
        return ()

    def tag_template(self):
        return self.raw['tag_template']

    def build_args(self):
        return self.raw['build_args']

    def target_name(self):
        return self.raw.get('target')

    def dockerfile_relpath(self):
        return self.raw['dockerfile']

    def builddir_relpath(self):
        return self.raw['dir']

    def resource_labels(self):
        # for base-component-descriptor
        return self.raw['resource_labels']

    def resource_name(self):
        parts = self.image_reference().split('/')
        # image references are lengthy (e.g. gcr.eu/<org>/<path>/../<name>)
        # -> shorten this a bit (keep domain and last part of url path)
        domain = parts[0]
        image_name = parts[-1]
        return '_'.join([self.name(), domain, image_name])

    def _children(self):
        return ()

    def validate(self):
        super().validate()
        for label in self.resource_labels():
            try:
                dacite.from_dict(
                    data_class=cm.Label,
                    data=label,
                    config=dacite.Config(strict=True),
                )
            except dacite.DaciteError as e:
                raise ModelValidationError(
                    f"Invalid '{label=}'."
                ) from e


class OciBuilder(enum.Enum):
    CONCOURSE_IMAGE_RESOURCE = 'concourse-image-resource'
    KANIKO = 'kaniko'


ATTRIBUTES = (
    AttributeSpec.required(
        name='dockerimages',
        doc='specifies the container images to be built',
        type=typing.Dict[str, PublishDockerImageDescriptor],
    ),
    AttributeSpec.optional(
        name='oci-builder',
        doc='specifies the container image builder to use',
        type=OciBuilder,
        default=OciBuilder.KANIKO,
    ),
)


class PublishTrait(Trait):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @classmethod
    def _attribute_specs(cls):
        return ATTRIBUTES

    def _children(self):
       return self.dockerimages()

    def dockerimages(self) -> typing.List[PublishDockerImageDescriptor]:
        image_dict = self.raw['dockerimages']

        return [
            PublishDockerImageDescriptor(name, args)
            for name, args
            in image_dict.items()
        ]

    def oci_builder(self) -> OciBuilder:
        return OciBuilder(self.raw['oci-builder'])

    def transformer(self):
        return PublishTraitTransformer(trait=self)


IMAGE_ENV_VAR_NAME = 'image_path'
TAG_ENV_VAR_NAME = 'tag_path'


class PublishTraitTransformer(TraitTransformer):
    name = 'publish'

    def __init__(self, trait: PublishTrait, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.trait = not_none(trait)
        self._build_steps = []

    def inject_steps(self):
        # 'publish' step
        publish_step = PipelineStep(
            name='publish',
            raw_dict={},
            is_synthetic=True,
            notification_policy=StepNotificationPolicy.NOTIFY_PULL_REQUESTS,
            injecting_trait_name=self.name,
            script_type=ScriptType.BOURNE_SHELL,
        )
        publish_step.set_timeout(duration_string='4h')

        # 'prepare' step
        prepare_step = PipelineStep(
            name='prepare',
            raw_dict={},
            is_synthetic=True,
            notification_policy=StepNotificationPolicy.NO_NOTIFICATION,
            injecting_trait_name=self.name,
            script_type=ScriptType.BOURNE_SHELL,
        )
        prepare_step.set_timeout(duration_string='30m')

        publish_step._add_dependency(prepare_step)

        if self.trait.oci_builder() is OciBuilder.KANIKO:
            with open(concourse.paths.last_released_tag_file) as f:
                last_tag = f.read().strip()
            kaniko_image_ref = f'eu.gcr.io/gardener-project/cc/job-image-kaniko:{last_tag}'

            for img in self.trait.dockerimages():
                build_step = PipelineStep(
                    name=f'build_oci_image_{img.name()}',
                    raw_dict={
                        'image': kaniko_image_ref,
                    },
                    is_synthetic=True,
                    notification_policy=StepNotificationPolicy.NOTIFY_PULL_REQUESTS,
                    injecting_trait_name=self.name,
                    script_type=ScriptType.PYTHON3,
                    extra_args={
                        'image_descriptor': img,
                    }
                )
                build_step._add_dependency(prepare_step)
                self._build_steps.append(build_step)
                yield build_step

                publish_step._add_dependency(build_step)

        yield prepare_step
        yield publish_step

    def process_pipeline_args(self, pipeline_args: JobVariant):
        main_repo = pipeline_args.main_repository()
        prepare_step = pipeline_args.step('prepare')

        if self.trait.oci_builder() is OciBuilder.CONCOURSE_IMAGE_RESOURCE:
            publish_step = pipeline_args.step('publish')

        image_name = main_repo.branch() + '-image'
        tag_name = main_repo.branch() + '-tag'

        # configure prepare step's outputs (consumed by publish step)
        prepare_step.add_output(variable_name=IMAGE_ENV_VAR_NAME, name=image_name)
        prepare_step.add_output(variable_name=TAG_ENV_VAR_NAME, name=tag_name)

        if self.trait.oci_builder() is OciBuilder.CONCOURSE_IMAGE_RESOURCE:
            # configure publish step's inputs (produced by prepare step)
            publish_step.add_input(variable_name=IMAGE_ENV_VAR_NAME, name=image_name)
            publish_step.add_input(variable_name=TAG_ENV_VAR_NAME, name=tag_name)

        for build_step in self._build_steps:
            build_step.add_input(variable_name=IMAGE_ENV_VAR_NAME, name=image_name)

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
            if step.name in ['publish', 'release', 'build_oci_image']:
                continue
            if step.name.startswith('build_oci_image'):
                continue
            prepare_step._add_dependency(step)

    @classmethod
    def dependencies(cls):
        return {'version'}
