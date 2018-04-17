from copy import deepcopy
import toposort

from util import merge_dicts
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

class DefinitionFactory(object):
    def __init__(self, raw_dict):
        self.raw_dict = ensure_not_none(raw_dict)

        if not 'variants' in raw_dict:
            raise ModelValidationError('at least one variant must be specified')

        # temporary hack: restore original structure (where base definition was every attribute
        # except for those named 'variants')
        # TODO: rework subsequent processing steps
        if 'base_definition' in self.raw_dict:
            for args_name, value in self.raw_dict['base_definition'].items():
                self.raw_dict[args_name] = value

            del self.raw_dict['base_definition']


    def create_pipeline_args(self):
        merged_variants_dict = self._create_variants_dict(self.raw_dict)

        variants = {}

        for variant_name, variant_dict in merged_variants_dict.items():
            variant = self._create_base_definition(raw_dict=variant_dict, variant_name=variant_name)
            self._apply_variant_specifics(variant_name, variant)
            variants[variant_name] = variant
            variant.validate()

        pipeline_definition = PipelineDefinition()
        pipeline_definition._variants_dict = variants

        return pipeline_definition


    def _create_variants_dict(self, raw_dict):
        variants_dict = normalise_to_dict(deepcopy(raw_dict['variants']))

        # everything but the variants attribute
        base_dict = deepcopy(raw_dict)
        del base_dict['variants']

        merged_variants = {}
        for variant_name, variant_args in variants_dict.items():
            # optimisation: if there are no variant-specific arguments, we do not need to merge
            if variant_args:
                merged_variants[variant_name] =  merge_dicts(base_dict, variant_args)
            else:
                merged_variants[variant_name] = deepcopy(base_dict)
            merged_variants[variant_name]['variant_args'] = variant_args

        return merged_variants

    def _apply_variant_specifics(self, variant_name: str, variant: 'PipelineArgs'):
        variant_args = variant.raw['variant_args']

        # add variant-specifics
        variant.variant_name = variant_name

        variant.variant_args = variant_args

        # post-process repositories
        repos = set(variant._repos_dict.values())

        self._apply_traits(variant)

        return variant


    def _create_base_definition(self, raw_dict, variant_name):
        base_def = PipelineArgs(raw_dict=raw_dict)

        # build steps
        base_def._steps_dict = self._create_build_steps(raw_dict)

        # traits
        base_def._traits_dict = self._create_traits(raw_dict, variant_name)

        # repositories (attention: those depend on build steps)
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
