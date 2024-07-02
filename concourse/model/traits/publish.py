# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import dataclasses
import enum
import textwrap
import typing

import dacite
import jsonpath_ng

from ci.util import not_none
from model import NamedModelElement
import concourse.model.traits.component_descriptor
import gci.componentmodel as cm
import oci.model as om

from concourse.model.job import (
    JobVariant,
)
from concourse.model.step import (
    PipelineStep,
    PrivilegeMode,
    PullRequestNotificationPolicy,
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


@dataclasses.dataclass
class TargetSpec:
    '''
    OCI Image build target:
    - target: the docker build target
    - image: the image push target (to where build result should be published)
    - name: image name (for use in component-descriptor)

    if name is not passed, a fallback is calculated from <publish.dockerimages.<name>> + target.
    '''
    target: str
    image: str
    name: str | None = None # default value is injected after deserialisation


IMG_DESCRIPTOR_ATTRIBS = (
    AttributeSpec.optional(
        name='registry',
        default=None,
        type=str,
        doc='name of the registry config to use when pushing the image.',
    ),
    AttributeSpec.optional(
        name='image',
        type=str,
        doc='''\
        image reference to publish the created container image to. required if on `targets` are set
        ''',
        default=None,
    ),
    AttributeSpec.optional(
        name='extra_push_targets',
        default=[],
        type=list[str],
        doc='''
        additional targets to publish built images to. Entries _may_ contain a tag (which is
        honoured, if present). Entries without a tag will use the same tag as the "main" image
        (defined by `image` attribute).
        only supported for docker or docker-buildx OCI-Builder. Must not be used in
        conjunction with `targets`.
        ''',
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
        name='prebuild_hook',
        default=None,
        doc='''
            if configured, a callback is executed prior to running image-build. the value is
            interpreted as relative path to main-repository root directory, and must be an
            executable file.
            It can be used, for example, to preprocess the "Dockerfile" to use, or to prepare
            contents within the build directory.
            The following environment variables are passed (all paths are absolute):
            - BUILD_DIR # path to build directory
            - DOCKERFILE # path to dockerfile

            Only supported for oci-builder docker or docker-buildx.
            dockerd will be available and running (docker excecutable accessible from PATH).
        ''',
        type=str,
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
        doc='''\
            only for multistage builds: the target up to which to build.
            must not be used if `targets` is defined.
        ''',
    ),
    AttributeSpec.optional(
        name='targets',
        default=None,
        doc='''\
            if set, the given targets are built in the given order in the same build environment.
            This is useful to reduce resource consumption for multiple builds sharing common
            prerequisite build steps.
            Only supported if oci-builder is set to `docker-buildx` or `docker`
        ''',
        type=list[TargetSpec],
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
    AttributeSpec.optional(
        name='platforms',
        doc=textwrap.dedent('''\
        If `platforms` is defined at toplevel, then defining it again for a single image-build
        can be done in order to only build this image for a subset of platforms.

        see toplevel documentation for `platforms` for reference.
        '''
        ),
        type=list[str],
        default=None,
    ),
)


class PublishDockerImageDescriptor(NamedModelElement, ModelDefaultsMixin, AttribSpecMixin):
    def __init__(
        self,
        *args,
        platform:str=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        if not isinstance(self.raw, dict):
            raise ModelValidationError(
                f'{self.__class__.__name__} expects a dict - got: {self.raw=}'
            )
        self._apply_defaults(raw_dict=self.raw)
        self._platform = platform

        if platform:
            self._base_name = self._name
            self._name += f'-{platform.replace("/", "-")}'
        else:
            self._base_name = self._name

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

    @property
    def extra_push_targets(self) -> list[om.OciImageReference]:
        return [om.OciImageReference(target) for target in self.raw['extra_push_targets']]

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

    @property
    def is_multitarget(self):
        if self.raw.get('targets'):
            return True
        return False

    @property
    def targets(self):
        if (target_name := self.target_name()):
            return (
                TargetSpec(
                    target=target_name,
                    image=self.image_reference(),
                    name=self._base_name,
                ),
            )
        if not (raw_targets := self.raw.get('targets')):
            return (
                TargetSpec(
                    target=None,
                    image=self.image_reference(),
                    name=self._base_name,
                ),
            )

        targets = []
        for raw_target in raw_targets:
            target = dacite.from_dict(
                data_class=TargetSpec,
                data=raw_target,
            )
            if not target.name:
                target.name = f'{self.name()}-{target.target}'

            targets.append(target)

        return targets

    @property
    def prebuild_hook(self) -> str | None:
        return self.raw['prebuild_hook']

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

    def platforms(self) -> list[str] | None:
        return self.raw['platforms']

    def platform(self) -> typing.Optional[str]:
        '''
        returns the target `platform` for which this image should be built, if tgt platform
        was explicitly configured in pipeline-definition.
        `None` indicates that no tgt platform was configured (in which case the platform
        should default to the runtime's own platform).
        '''
        return self._platform

    def _children(self):
        return ()

    def validate(self):
        super().validate()

        if self.target_name() and self.raw.get('targets', False):
            raise ModelValidationError('target and targets must not both be set')

        if self.extra_push_targets and self.raw.get('targets', False):
            raise ModelValidationError('targets and extra_push_targets must not both be set')

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
    DOCKER = 'docker'
    DOCKER_BUILDX = 'docker-buildx'


@dataclasses.dataclass
class HelmchartValueMapping:
    '''
    a mapping between OCM-Resources and values to be passed to a helmchart.

    Typically, this will be a reference to an OCI Image mapped to attributes within a
    "values.yaml" for helm.

    Mappings are evaluated as preprocessing step before creating
    helmchart-archive (i.e. values.yaml is patched). They are also incorporated into resulting
    OCM Component-Descriptors.

    ref: reference to (virtual) attribute of an OCM-Resource
    attribute: JSONPath-spec

    Syntax for ref:

        oci-resource:<name>.<attribute>
        ^^^^^
        prefix

        name: resource-name (match names used in `dockerimages` attribute)
        attribute: choose either of:
            repository: image-reference without tag
            tag:        tag (either symbolic tag, or digest)
            digest:     image-digest (including algorithm-prefix, e.g. sha256:...)
            image:      image-reference including tag

    Example:

    Assuming there is a component-descriptor with an OciImage-Resource named `my-image` that
    should be mapped to attributes `image.repository` and `image.tag` in values.yaml.

    - ref: ocm-resource:my-image.repository
      attribute: image.repository
    - ref: ocm-resource:my-image.tag
      attribute: image.tag
    '''
    ref: str
    attribute: str

    @property
    def referenced_resource_and_attribute(self):
        '''
        returns a two-tuple of reference resource-name and resource's attribute, parsed from
        `ref`. If ref is not a valid ref (see class-docstr), raises ValueError.
        '''
        if not self.ref.startswith(prefix := 'ocm-resource:'):
            raise ValueError(self.ref, f'must start with {prefix=}')

        ref = self.ref.removeprefix(prefix)
        parts = ref.split('.')
        if not len(parts) == 2:
            raise ValueError(ref, 'must consist of exactly two period-separated parts')

        resource_name, attribute_name = parts
        return resource_name, attribute_name


@dataclasses.dataclass
class HelmchartCfg:
    '''
    A configuration for a helmchart that should be published. Data is also injected into
    component-descriptor (if trait is present).

    name:     Chart's name (also used as resource-name for component-descriptor); must be unique
    registry: OCI Registry prefix to publish helmchart to (<name>:<version> will be appended)
    dir:      relative path to directory containing helmchart
    mappings: attributes to patch in values.yaml
    '''
    name: str
    registry: str
    dir: str
    mappings: list[HelmchartValueMapping] = dataclasses.field(default_factory=list)


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
        default=OciBuilder.DOCKER,
    ),
    AttributeSpec.optional(
        name='no-buildkit',
        doc='if using `docker` as oci-builder, force to not use buildkit - ignored otherwise',
        type=bool,
        default=False,
    ),
    AttributeSpec.optional(
        name='platforms',
        doc=textwrap.dedent('''\
        if defined, all image-builds will be done for each of the specified platforms, which
        may result in cross-platform builds. Only supported if using `docker-buildx` as oci-builder.

        As an implementation detail that may change in the future, multiarch/quemu-user-static
        `(see) <https://github.com/multiarch/qemu-user-static>`_ is used. As the underlying
        CICD nodes use `Linux` as kernel, only the architecture may be chosen. The following
        platforms are supported:

        - linux/386
        - linux/amd64 # aka x86_64
        - linux/arm/v6
        - linux/arm/v7
        - linux/arm64
        - linux/ppc64le
        - linux/riscv64
        - linux/s390x

        The resulting images will receive tags derived from the default tag (single-image case)
        with a suffix containing the platform.

        The default tag will be published as multi-arch image, referencing all platform-variants.
        This will also hold true if only one platform is specified.

        .. note::
            if specifying a list of platforms, _all_ platforms (including the default platform)
            must be explicitly specified.
        '''
        ),
        type=list[str],
        default=None,
    ),
    AttributeSpec.optional(
        name='helmcharts',
        doc='''\
        ''',
        type=tuple[HelmchartCfg],
        default=(),
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

        if not (platforms := self.platforms()):
            platforms = (None, )

        def matches_platform(platform, image_args):
            if platform is None:
                return True

            if (platforms := image_args.get('platforms', None)) is None:
                return True

            if platform in platforms:
                return True

            # special-case: normalise x86_64 -> arm64
            if platform == 'linux/x86_64':
                return 'linux/amd64' in platforms
            if platform == 'linux/arm64':
                return 'linux/x86_64' in platforms

        for platform in platforms:
            yield from (
                PublishDockerImageDescriptor(
                    name,
                    args,
                    platform=platform,
                )
                for name, args
                in image_dict.items()
                if matches_platform(platform=platform, image_args=args)
            )

    def platforms(self) -> typing.Optional[list[str]]:
        '''
        the list of explicitly configured build platforms
        guaranteed to be either non-empty, or None
        '''
        return self.raw.get('platforms', None)

    def oci_builder(self) -> OciBuilder:
        return OciBuilder(self.raw['oci-builder'])

    def use_buildkit(self) -> bool:
        return not self.raw['no-buildkit']

    @property
    def helmcharts(self) -> tuple[HelmchartCfg]:
        return tuple(
            dacite.from_dict(
                HelmchartCfg,
                data=raw,
            )
            for raw in self.raw['helmcharts']
        )

    def transformer(self):
        return PublishTraitTransformer(trait=self)

    def validate(self):
        super().validate()

        if self.platforms() and not self.oci_builder() is OciBuilder.DOCKER_BUILDX:
            raise ModelValidationError(
                'must not specify platforms unless using docker-buildx as oci-builder'
            )
        if not self.platforms() and not self.platforms() is None:
            raise ModelValidationError(
                'must not specify empty list of platforms (omit attr instead)'
            )

        image_names = set()
        for image in self.dockerimages():
            if image.name() in image_names:
                raise ModelValidationError(
                    f'duplicate image: {image.name()=}',
                )
            image_names.add(image.name())

        helmchart_names = set()
        for helmchart in self.helmcharts:
            if helmchart.name in helmchart_names:
                raise ModelValidationError(
                    f'duplicate resource: {helmchart.name=}',
                )
            helmchart_names.add(helmchart.name)

            for mapping in helmchart.mappings:
                if not mapping.ref.startswith('ocm-resource:'):
                    raise ModelValidationError(
                        mapping,
                        'mapping.ref must start with ocm-resource:',
                    )

                try:
                    jsonpath_ng.parse(mapping.attribute)
                except jsonpath_ng.exceptions.JSONPathError as jpe:
                    raise ModelValidationError(
                        jpe,
                        mapping.attribute,
                    )

                _, attr = mapping.referenced_resource_and_attribute
                if not attr in (
                    'repository',
                    'tag',
                    'image',
                ):
                    raise ModelValidationError(
                        mapping.ref,
                        'mapping.ref\'s attribute must be one of repository, tag, image',
                    )


class PublishTraitTransformer(TraitTransformer):
    name = 'publish'

    def __init__(
        self,
        trait: PublishTrait,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.trait = not_none(trait)
        cfg_set = trait.cfg_set

        # XXX workaround for documentation-rendering
        if cfg_set:
            self.worker_node_cfg = cfg_set.concourse().worker_node_cfg
        self._build_steps = []

    def inject_steps(self):
        publish_step = PipelineStep(
            name='publish',
            raw_dict={},
            is_synthetic=True,
            injecting_trait_name=self.name,
            script_type=ScriptType.PYTHON3,
            extra_args={
                'publish_trait': self.trait,
            },
        )
        publish_step.set_timeout(duration_string='4h')
        self._publish_step = publish_step

        # 'prepare' step
        prepare_step = PipelineStep(
            name='prepare',
            raw_dict={},
            is_synthetic=True,
            pull_request_notification_policy=PullRequestNotificationPolicy.NO_NOTIFICATION,
            injecting_trait_name=self.name,
            script_type=ScriptType.BOURNE_SHELL,
        )
        prepare_step.set_timeout(duration_string='30m')

        publish_step._add_dependency(prepare_step)

        if self.trait.helmcharts:
            helmcharts_step = PipelineStep(
                name='helmcharts',
                raw_dict={},
                is_synthetic=True,
                injecting_trait_name=self.name,
                script_type=ScriptType.PYTHON3,
                extra_args={
                    'publish_trait': self.trait,
                },
            )
            helmcharts_step._add_dependency(prepare_step)
            helmcharts_step.add_input(
                name=concourse.model.traits.component_descriptor.DIR_NAME,
                variable_name=concourse.model.traits.component_descriptor.ENV_VAR_NAME,
            )
            helmcharts_step.add_output(
                name='helmcharts',
                variable_name='helmcharts',
            )
            yield helmcharts_step

        if (oci_builder := self.trait.oci_builder()) in (
            OciBuilder.DOCKER,
            OciBuilder.DOCKER_BUILDX,
        ):
            if oci_builder in (OciBuilder.DOCKER, OciBuilder.DOCKER_BUILDX):
                pass
            else:
                raise NotImplementedError(oci_builder)

            for img in self.trait.dockerimages():
                worker_node_tags = ()

                if platform_name := img.platform():
                    platform = self.worker_node_cfg.platform_for_oci_platform(
                        oci_platform_name=platform_name,
                    )
                    if platform and platform.worker_tag:
                        worker_node_tags = (platform.worker_tag,)
                else:
                    platform = None

                build_step = PipelineStep(
                    name=f'build_oci_image_{img.name()}',
                    raw_dict={
                        'privilege_mode': PrivilegeMode.PRIVILEGED
                            if oci_builder in (OciBuilder.DOCKER, OciBuilder.DOCKER_BUILDX)
                            else PrivilegeMode.UNPRIVILEGED,
                    },
                    is_synthetic=True,
                    injecting_trait_name=self.name,
                    script_type=ScriptType.PYTHON3,
                    worker_node_tags=worker_node_tags,
                    platform=platform,
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

        image_name = main_repo.branch() + '-image'
        tag_name = main_repo.branch() + '-tag'

        image_path = 'image_path'
        tag_path = 'tag_path'

        # configure prepare step's outputs (consumed by publish step)
        prepare_step.add_output(variable_name=image_path, name=image_name)
        prepare_step.add_output(variable_name=tag_path, name=tag_name)

        for build_step in self._build_steps + [self._publish_step]:
            build_step.add_input(variable_name=image_path, name=image_name)

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
            if step.name in ['publish', 'release', 'build_oci_image', 'helmcharts']:
                continue
            if step.name.startswith('build_oci_image'):
                continue
            if 'publish' in step.trait_depends():
                # don't depend on steps that have been explicitly configured to depend on publish.
                continue
            prepare_step._add_dependency(step)

    @classmethod
    def dependencies(cls):
        return {'version'}
