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

import copy
import enum
import graphlib
import typing

from concourse.model.base import (
    ModelBase,
    select_attr,
)
from ci.util import not_none
from concourse.model.resources import RepositoryConfig
import concourse.model.step


_not_set = object() # sentinel


class AbortObsoleteJobs(enum.Enum):
    ALWAYS = 'always'
    ON_FORCE_PUSH_ONLY = 'on_force_push_only'
    NEVER = 'never'


class JobVariant(ModelBase):
    def __init__(self, name: str, raw_dict: dict, resource_registry, *args, **kwargs):
        self._main_repository_name = None
        self._resource_registry = not_none(resource_registry)
        self.variant_name = name
        self._publish_repos_dict = {}
        self._repos_dict = {}
        self._traits_dict = {}
        self._steps_dict = {}
        super().__init__(raw_dict=raw_dict, *args, **kwargs)

    @classmethod
    def _attribute_specs(cls):
        return ()

    def _known_attributes(self):
        return {
            'abort_obsolete_jobs',
            'repo',
            'repos',
            'steps',
            'traits',
        }

    def _children(self):
        yield from self.steps()
        yield from self.traits().values()
        yield from self.repositories()

    def traits(self):
        return self._traits_dict

    def trait(self, name, default=_not_set):
        trait =  self._traits_dict.get(name, default)
        if trait is _not_set:
            raise KeyError(f'trait {name=} not present for {self.job_name()=}')
        return trait

    def has_trait(self, name):
        return name in self.traits()

    def job_name(self):
        parts = []
        if self.has_main_repository():
            parts.append(self.main_repository().branch())
        parts.append(self.variant_name)
        parts.append('job')

        return '-'.join(parts)

    def steps(self):
        return self._steps_dict.values()

    def step_names(self):
        return map(select_attr('name'), self.steps())

    def _step_depends_on(
        self,
        step_name,
        step_dependency_name,
        dependencies,
        visited_steps=None
    ):
        '''return whether `step_name` depends on `step_dependency_name` given the passed
        dependencies-dictionary.

        Only considers steps defined by users.
        '''
        if not visited_steps:
            visited_steps = set()

        if step_name in visited_steps:
            return False

        visited_steps.add(step_name)

        if self.step(step_name).is_synthetic:
            return False
        # if step is not present in dependencies-dict, it has no relevant dependencies
        if not step_name in dependencies:
            return False
        if step_dependency_name in dependencies[step_name]:
            return True

        return any([
            self._step_depends_on(s, step_dependency_name, dependencies, visited_steps)
            for s in dependencies[step_name]
        ])

    def _find_and_resolve_publish_trait_circular_dependencies(
        self,
        dependencies: dict[str, set[str]],
        cycle_info: typing.Sequence[str],
    ):
        '''
        @param dependencies: dict of {step_name: dependencies} as understood by TopologicalSorter
        @cycle_info: sequence of step-names with circular dependency
        '''
        # handle circular dependencies caused by depending on the publish step, e.g.:
        # test -> publish -> ... -> prepare -> test
        updated_dependencies = copy.deepcopy(dependencies)
        custom_steps_depending_on_publish = [
            step_name for step_name in cycle_info
            if self._step_depends_on(
                step_name=step_name,
                step_dependency_name='publish',
                dependencies=updated_dependencies,
            )
        ]

        if 'prepare' in cycle_info and custom_steps_depending_on_publish:
            for step_name in custom_steps_depending_on_publish:
                if step_name in updated_dependencies['prepare']:
                    updated_dependencies['prepare'].remove(step_name)

        return updated_dependencies

    def _find_and_resolve_release_trait_circular_dependencies(
        self,
        dependencies,
        cycle_info: typing.Sequence[str],
    ):
        updated_dependencies = copy.deepcopy(dependencies)
        if (
            len(cycle_info) == 3
            and len(unique_steps := set(cycle_info)) == 2
            and 'release' in unique_steps # be defensive
        ):
            # We have a cycle that is represented as step -> release -> step. These
            # steps can actually be resolved easily by removing the dependency of the release-step
            # as they are only possible if the pipeline definition explicitly specifies
            # a step to depend on the release-trait.
            first_name, second_name = unique_steps
            if first_name == 'release':
                updated_dependencies[first_name].remove(second_name)
            else:
                # 'release' was the second entry
                updated_dependencies[second_name].remove(first_name)
        else:
            for step_name in cycle_info:
                step = self.step(step_name)
                if not step.is_synthetic:
                    continue # only patch away synthetic steps' dependencies

                for step_dependency_name in step._dependencies():
                    step_dependency = self.step(step_dependency_name)
                    if step_dependency.is_synthetic:
                        continue # leave dependencies between synthetic steps
                    # patch out dependency from synthetic step to custom step
                    if step_dependency_name in updated_dependencies[step_name]:
                        updated_dependencies[step_name].remove(step_dependency_name)

        return updated_dependencies

    def ordered_steps(self) -> typing.Generator[tuple[str], None, None]:
        dependencies = {
            step.name: step.depends() for step in self.steps()
        }
        # add dependencies on trait-defined steps
        for step in self.steps():
            dependencies[step.name] |= {
                s.name for s in self.steps()
                if s.injecting_trait_name() in step.trait_depends()
            }

        def iter_results(toposorter: graphlib.TopologicalSorter):
            while toposorter.is_active():
                ready_tasks = tuple(toposorter.get_ready())
                toposorter.done(*ready_tasks)
                yield ready_tasks

        try:
            toposorter = graphlib.TopologicalSorter(graph=dependencies)
            toposorter.prepare()
            return iter_results(toposorter=toposorter)
        except graphlib.CycleError as ce:
            cycle_steps = ce.args[1] # contains a list of circular steps
            dependencies = self._find_and_resolve_publish_trait_circular_dependencies(
                dependencies,
                cycle_info=cycle_steps,
            )
            try:
                # check whether resolving the dependency between the publish trait has already
                # fixed the issue
                toposorter = graphlib.TopologicalSorter(graph=dependencies)
                toposorter.prepare()
                return iter_results(toposorter=toposorter)
            except graphlib.CycleError as ce:
                cycle_steps = ce.args[1] # contains a list of circular steps
                dependencies = self._find_and_resolve_release_trait_circular_dependencies(
                    dependencies,
                    cycle_info=cycle_steps,
                )
                # try again - if there is still a cyclic dependency, this is probably caused
                # by a user error - so let it propagate
                toposorter = graphlib.TopologicalSorter(graph=dependencies)
                toposorter.prepare()
                return iter_results(toposorter=toposorter)

    def add_step(self, step: concourse.model.step.PipelineStep): # noqa
        if self.has_step(step.name):
            raise ValueError('conflict: pipeline definition already contained step {s}'.format(
                s=step.name
            )
            )
        self._steps_dict[step.name] = step

    def step(self, name) -> concourse.model.step.PipelineStep:
        if not (step := self._steps_dict.get(name)):
            raise ValueError(f'no such step: {name=}')
        return step

    def has_step(self, step_name):
        return step_name in self.step_names()

    def pr_repository(self, name):
        pr_repo = self.repository(name)
        return RepositoryConfig(
            raw_dict=dict(pr_repo.raw),
            logical_name=name,
            qualifier='pr',
            is_pull_request=True
        )

    def repositories(self):
        # TODO: introduce a common base class for "input resources"
        # (where Github and PR are two examples, and "time" will be the third)
        return self._repos_dict.values()

    def repository_names(self):
        return self._repos_dict.keys()

    def repository(self, name):
        return self._repos_dict[name]

    def has_main_repository(self):
        return self._main_repository_name is not None

    def main_repository(self):
        return self.repository(self._main_repository_name)

    def publish_repositories(self):
        return self._publish_repos_dict.values()

    def publish_repository(self, name):
        return self._publish_repos_dict[name]

    def has_publish_repository(self, name):
        return name in self._publish_repos_dict

    def __repr__(self):
        return f'JobVariant: {self.variant_name}'
