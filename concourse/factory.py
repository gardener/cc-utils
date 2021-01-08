from copy import deepcopy
from itertools import chain
import toposort

from ci.util import merge_dicts, not_none
from model.base import ModelValidationError
from concourse.model.step import (
    PipelineStep,
    StepNotificationPolicy,
)
from concourse.model.base import (
        normalise_to_dict,
        ScriptType,
)
from concourse.validator import PipelineDefinitionValidator
from concourse.model.job import JobVariant
from concourse.model.pipeline import PipelineDefinition
from concourse.model.resources import (
    RepositoryConfig,
    ResourceRegistry,
)
from concourse.model.traits import TraitsFactory
from concourse.model.traits.meta import MetaTraitTransformer


def ensure_dict(d, allow_empty=True):
    if allow_empty and d is None:
        return {}
    if not allow_empty and not d:
        raise ModelValidationError('a non-empty dict is required')
    if not isinstance(d, dict):
        raise ModelValidationError('a dict is required')
    return d


class RawPipelineDefinitionDescriptor:
    '''
    Container type holding a single (raw) pipeline definition and metadata.
    Basic value validation is done in the c'tor.
    '''

    def __init__(
        self,
        name,
        base_definition,
        variants,
        exception=None,
        template='default'
    ):
        self.name = not_none(name)
        self.base_definition = ensure_dict(base_definition, allow_empty=True)
        self.variants = ensure_dict(variants, allow_empty=False)
        self.template = not_none(template)
        self.exception = exception


class DefinitionFactory:
    '''
    Creates `PipelineDefinition` instances from "raw" `PipelineDefinitionDescriptor`s.

    "Raw" definitions are associative arrays of a certain structure. They are read from
    declaring components and are typically maintained by (human) component owners.

    Definitions feature an (optional) two-level inheritance hierarchy: A base definition and
    an arbitrary set of variants. At least one variant is required. Variants represent concrete
    single job definitions. Attributes defined in a base definition are inherited into each
    variant. Variants may overwrite inherited attributes.
    '''

    def __init__(
        self,
        raw_definition_descriptor: RawPipelineDefinitionDescriptor,
        cfg_set,
    ):
        self.raw_definition_descriptor = not_none(raw_definition_descriptor)
        self.cfg_set = cfg_set

    def create_pipeline_definition(self) -> PipelineDefinition:
        merged_variants_dict = self._create_variants_dict(self.raw_definition_descriptor)

        resource_registry = ResourceRegistry()
        variants = {}

        for variant_name, variant_dict in merged_variants_dict.items():
            variant = self._create_variant(
                raw_dict=variant_dict,
                variant_name=variant_name,
                resource_registry=resource_registry,
            )
            self._apply_traits(variant)

            # collect repositories
            for repo in chain(variant._repos_dict.values(), variant._publish_repos_dict.values()):
                if repo in resource_registry:
                    existing_repo = resource_registry.resource(repo)
                    # hack: patch-in should-trigger (the proper way to implement this
                    # would be to separate (effective) resource-definitions from additional
                    # resource-specialisations as contained in variants
                    existing_repo._trigger |= repo.should_trigger()
                else:
                    resource_registry.add_resource(deepcopy(repo), discard_duplicates=False)

            variants[variant_name] = variant

        pipeline_definition = PipelineDefinition()
        pipeline_definition._variants_dict = variants
        pipeline_definition._resource_registry = resource_registry

        validator = PipelineDefinitionValidator(pipeline_definition=pipeline_definition)
        validator.validate()

        return pipeline_definition

    def _create_variants_dict(self, raw_definition_descriptor):
        variants_dict = normalise_to_dict(deepcopy(raw_definition_descriptor.variants))

        base_dict = deepcopy(raw_definition_descriptor.base_definition)

        merged_variants = {}
        for variant_name, variant_args in variants_dict.items():
            # optimisation: if there are no variant-specific arguments, we do not need to merge
            if variant_args:
                merged_variants[variant_name] =  merge_dicts(base_dict, variant_args)
            else:
                merged_variants[variant_name] = deepcopy(base_dict)

        return merged_variants

    def _create_variant(self, raw_dict, variant_name, resource_registry) -> JobVariant:
        variant = JobVariant(
            name=variant_name,
            raw_dict=raw_dict,
            resource_registry=resource_registry
        )

        # build steps
        variant._steps_dict = self._create_build_steps(raw_dict)

        # traits
        variant._traits_dict = self._create_traits(raw_dict, variant_name)

        self._create_repos(variant, raw_dict)
        self._inject_publish_repos(variant)

        return variant

    def _apply_traits(self, pipeline_def):
        transformers = [trait.transformer() for trait in pipeline_def._traits_dict.values()]
        transformers_dict = {t.name: t for t in transformers}
        transformer_names = set(transformers_dict.keys())

        for transformer in transformers:
            if not set(transformer.dependencies()).issubset(transformer_names):
                missing = set(transformer.dependencies()) - transformer_names
                raise ModelValidationError(
                    f'{pipeline_def}: trait requires missing traits: ' + ', '.join(missing)
                )

        # order transformers according to dependencies
        transformer_dependencies = {
            t.name: t.order_dependencies() & transformer_names for t in transformers
        }

        ordered_transformers = []
        for name in toposort.toposort_flatten(transformer_dependencies):
            ordered_transformers.append(transformers_dict[name])

        # hardcode meta trait transformer
        ordered_transformers.append(MetaTraitTransformer())

        # inject new steps
        for transformer in ordered_transformers:
            for step in transformer.inject_steps():
                pipeline_def.add_step(step)

        # do remaining processing
        for transformer in ordered_transformers:
            transformer.process_pipeline_args(pipeline_def)

    def _create_traits(self, raw_dict, variant_name):
        if 'traits' not in raw_dict:
            raw_dict['traits'] = {}

        if 'options' not in raw_dict['traits']:
            raw_dict['traits']['options'] = {}
        if 'notifications' not in raw_dict['traits']:
            raw_dict['traits']['notifications'] = {}

        traits_args = normalise_to_dict(raw_dict['traits'])
        traits_dict = {
                name: TraitsFactory.create(
                    name=name,
                    variant_name=variant_name,
                    args_dict=args if args else {},
                    cfg_set=self.cfg_set,
                )
                for name, args in traits_args.items()
        }
        return traits_dict

    def _create_build_steps(self, raw_dict):
        steps_dict = {}
        if 'steps' not in raw_dict:
            return steps_dict
        elif not raw_dict['steps']:
            return {}

        for stepname, step in raw_dict['steps'].items():
            if step is None:
                raw_dict['steps'][stepname] = {}

        steps_dict = {
            n: self._create_build_step(name=n, step_dict=sd) for n,sd in raw_dict['steps'].items()
        }
        return steps_dict

    def _create_build_step(self, name: str, step_dict: dict):
        return PipelineStep(
            name=name,
            is_synthetic=False,
            notification_policy=StepNotificationPolicy.NOTIFY_PULL_REQUESTS,
            raw_dict=step_dict,
            script_type=ScriptType.BOURNE_SHELL,
        )

    def _create_repos(self, pipeline_def: JobVariant, raw_dict):
        pipeline_def._repos_dict = {}
        if 'repo' in raw_dict:
            # special case: repo singleton (will vanish once we mv definitions into component-repos)
            repo_dict = raw_dict['repo']
            name = 'source' if 'name' not in repo_dict else repo_dict['name']
            pipeline_def._repos_dict[name] = RepositoryConfig(
                raw_dict=repo_dict,
                logical_name=name,
                qualifier=None,
                is_main_repo=True
            )
            pipeline_def._main_repository_name = name
        if 'repos' in raw_dict:
            for repo_dict in raw_dict['repos']:
                if not 'cfg_name' in repo_dict:
                    github_cfg = self.cfg_set.github()
                else:
                    github_cfg = self.cfg_set.github(repo_dict['cfg_name'])

                hostname = github_cfg.hostname()
                repo_dict['hostname'] = hostname

                pipeline_def._repos_dict.update({
                    repo_dict['name']: RepositoryConfig(
                        logical_name=repo_dict['name'],
                        raw_dict=repo_dict, is_main_repo=False
                    )
                })

    def _inject_publish_repos(self, pipeline_def):
        # synthesise "put-repositories"
        pipeline_def._publish_repos_dict = {}
        for step in filter(lambda step: step.publish_repository_names(), pipeline_def.steps()):
            for repo_name in step.publish_repository_names():
                # we need to clone the existing repository configuration
                source_repo = pipeline_def._repos_dict[repo_name]
                publish_repo = RepositoryConfig(
                    raw_dict=dict(source_repo.raw),
                    logical_name=repo_name,
                    qualifier='output',
                    is_main_repo=source_repo.is_main_repo(),
                )
                pipeline_def._publish_repos_dict[repo_name] = publish_repo
