from copy import deepcopy
import toposort

from util import merge_dicts
from model import ModelValidationError
from concourse.pipelines.modelbase import (
        PipelineStep,
        normalise_to_dict,
        ensure_not_none,
)
from concourse.pipelines.model import (
        PipelineArgs,
        PipelineDefinition,
)
from concourse.pipelines.model.repositories import RepositoryConfig
from concourse.pipelines.model.traits import TraitsFactory

def ensure_dict(d, allow_empty=True):
    if allow_empty and d is None:
        return {}
    if not allow_empty and not d:
        raise ModelValidationError('a non-empty dict is required')
    if not isinstance(d, dict):
        raise ModelValidationError('a dict is required')
    return d

class RawPipelineDefinitionDescriptor(object):
    '''
    Container type holding a single (raw) pipeline definition and metadata.
    Basic value validation is done in the c'tor.
    '''
    def __init__(self, name, base_definition, variants):
        self.name = ensure_not_none(name)
        self.base_definition = ensure_dict(base_definition, allow_empty=True)
        self.variants = ensure_dict(variants, allow_empty=False)


class DefinitionFactory(object):
    '''
    Creates `PipelineDefinition` instances from "raw" `PipelineDefinitionDescriptor`s.

    "Raw" definitions are associative arrays of a certain structure. They are read from
    declaring components and are typically maintained by (human) component owners.

    Definitions feature an (optional) two-level inheritance hierarchy: A base definition and
    an arbitrary set of variants. At least one variant is required. Variants represent concrete
    single job definitions. Attributes defined in a base definition are inherited into each
    variant. Variants may overwrite inherited attributes.
    '''
    def __init__(self, raw_definition_descriptor: RawPipelineDefinitionDescriptor):
        self.raw_definition_descriptor = ensure_not_none(raw_definition_descriptor)

    def create_pipeline_definition(self) -> PipelineDefinition:
        merged_variants_dict = self._create_variants_dict(self.raw_definition_descriptor)

        variants = {}

        for variant_name, variant_dict in merged_variants_dict.items():
            variant = self._create_variant(raw_dict=variant_dict, variant_name=variant_name)
            self._apply_traits(variant)
            variants[variant_name] = variant
            variant.validate()

        pipeline_definition = PipelineDefinition()
        pipeline_definition._variants_dict = variants

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


    def _create_variant(self, raw_dict, variant_name) -> PipelineArgs:
        base_def = PipelineArgs(name=variant_name, raw_dict=raw_dict)

        # build steps
        base_def._steps_dict = self._create_build_steps(raw_dict)

        # traits
        base_def._traits_dict = self._create_traits(raw_dict, variant_name)

        self._create_repos(base_def, raw_dict)
        self._inject_publish_repos(base_def)

        return base_def

    def _apply_traits(self, pipeline_def):
        transformers = [trait.transformer() for trait in pipeline_def._traits_dict.values()]
        transformers_dict = {t.name: t for t in transformers}
        transformer_names = set(transformers_dict.keys())

        # order transformers according to dependencies
        transformer_dependencies = {
            t.name: t.dependencies() & transformer_names for t in transformers
        }

        ordered_transformers = []
        for name in toposort.toposort_flatten(transformer_dependencies):
            ordered_transformers.append(transformers_dict[name])


        # inject new steps
        for transformer in ordered_transformers:
            for step in transformer.inject_steps():
                pipeline_def.add_step(step)

        # do remaining processing
        for transformer in ordered_transformers:
            transformer.process_pipeline_args(pipeline_def)

    def _create_traits(self, raw_dict, variant_name):
        if 'traits' in raw_dict:
            traits_args = normalise_to_dict(raw_dict['traits'])
            traits_dict = {
                    name: TraitsFactory.create(
                        name=name,
                        variant_name=variant_name,
                        args_dict=args if args else {}
                    )
                    for name, args in traits_args.items()
            }
            return traits_dict
        else:
            return {}


    def _create_build_steps(self, raw_dict):
        steps_dict = {}
        if not 'steps' in raw_dict:
            return steps_dict
        elif not raw_dict['steps']:
            return {}

        for stepname, step in raw_dict['steps'].items():
            if step is None:
                raw_dict['steps'][stepname] = {}

        steps_dict = {n:PipelineStep(name=n, raw_dict=sd) for n,sd in raw_dict['steps'].items()}
        return steps_dict

    def _create_repos(self, pipeline_def, raw_dict):
        pipeline_def._repos_dict = {}
        if 'repo' in raw_dict:
            # special case: repo singleton (will vanish once we mv definitions into component-repos)
            repo_dict = raw_dict['repo']
            name = 'source' if not 'name' in repo_dict else repo_dict['name']
            pipeline_def._repos_dict[name] =  RepositoryConfig(
                raw_dict=repo_dict,
                name=name,
                is_main_repo=True
            )
            pipeline_def._main_repository_name = name
        if 'repos' in raw_dict:
            pipeline_def._repos_dict.update({
                cfg_dict['name']: RepositoryConfig(raw_dict=cfg_dict, is_main_repo=False)
                for cfg_dict in raw_dict['repos']
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
                    name=repo_name + '-output',
                    logical_name=repo_name
                )
                pipeline_def._publish_repos_dict[repo_name] = publish_repo
