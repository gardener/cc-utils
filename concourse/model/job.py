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
import toposort

from concourse.model.base import (
    ModelBase,
    select_attr,
)
from ci.util import not_none
from concourse.model.resources import RepositoryConfig, ResourceIdentifier


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

    def meta_resource_name(self):
        meta_res = self._resource_registry.resource(
            ResourceIdentifier(type_name='meta', base_name=self.variant_name)
        )
        return meta_res.resource_identifier().name()

    def steps(self):
        return self._steps_dict.values()

    def step_names(self):
        return map(select_attr('name'), self.steps())

    def _step_depends_on(self, step_name, step_dependency_name, dependencies, visited_steps=None):
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

    def _find_and_resolve_publish_trait_circular_dependencies(self, dependencies, cycle_info):
        # handle circular dependencies caused by depending on the publish step, e.g.:
        # test -> publish -> ... -> prepare -> test
        updated_dependencies = copy.deepcopy(dependencies)
        custom_steps_depending_on_publish = [
            step_name for step_name in cycle_info
            if self._step_depends_on(step_name, 'publish', cycle_info)
        ]
        if (
            'prepare' in cycle_info
            and 'publish' in cycle_info
            and custom_steps_depending_on_publish
        ):
            for step_name in custom_steps_depending_on_publish:
                if step_name in updated_dependencies['prepare']:
                    updated_dependencies['prepare'].remove(step_name)

        return updated_dependencies

    def _find_and_resolve_release_trait_circular_dependencies(self, dependencies, cycle_info):
        updated_dependencies = copy.deepcopy(dependencies)
        for step_name, step_dependencies in cycle_info.items():
            step = self.step(step_name)
            if not step.is_synthetic:
                continue # only patch away synthetic steps' dependencies
            for step_dependency_name in step_dependencies:
                step_dependency = self.step(step_dependency_name)
                if step_dependency.is_synthetic:
                    continue # leave dependencies between synthetic steps
                # patch out dependency from synthetic step to custom step
                if step_dependency_name in updated_dependencies[step_name]:
                    updated_dependencies[step_name].remove(step_dependency_name)

        return updated_dependencies

    def ordered_steps(self):
        dependencies = {
            step.name: step.depends() for step in self.steps()
        }
        # add dependencies on trait-defined steps
        for step in self.steps():
            dependencies[step.name] |= {
                s.name for s in self.steps()
                if s.injecting_trait_name() in step.trait_depends()
            }
        try:
            result = list(toposort.toposort(dependencies))
        except toposort.CircularDependencyError as de:

            dependencies = self._find_and_resolve_publish_trait_circular_dependencies(
                dependencies,
                cycle_info=de.data,
            )
            dependencies = self._find_and_resolve_release_trait_circular_dependencies(
                dependencies,
                cycle_info=de.data,
            )
            # try again - if there is still a cyclic dependency, this is probably caused
            # by a user error - so let it propagate
            result = list(toposort.toposort(dependencies))

        # result contains a generator yielding tuples of step name in the correct execution order.
        # each tuple can/should be parallelised
        return result

    def add_step(self, step: 'PipelineStep'): # noqa
        if self.has_step(step.name):
            raise ValueError('conflict: pipeline definition already contained step {s}'.format(
                s=step.name
            )
            )
        self._steps_dict[step.name] = step

    def step(self, name) -> 'PipelineStep':
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
