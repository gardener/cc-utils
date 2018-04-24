import toposort

from concourse.pipelines.modelbase import (
    ModelBase,
    select_attr,
)
from concourse.pipelines.model.resources import RepositoryConfig

class PipelineArgs(ModelBase):
    def __init__(self, name: str, raw_dict: dict, *args, **kwargs):
        self._main_repository_name = None
        self.variant_name = name
        super().__init__(raw_dict=raw_dict, *args, **kwargs)

    def traits(self):
        return self._traits_dict

    def trait(self, name):
        return self._traits_dict[name]

    def has_trait(self, name):
        return name in self.traits()

    def steps(self):
        return self._steps_dict.values()

    def step_names(self):
        return map(select_attr('name'), self.steps())

    def ordered_steps(self):
        dependencies = {
            step.name: step.depends() for step in self.steps()
        }
        result = toposort.toposort(dependencies)
        # result contains a generator yielding tuples of step name in the correct execution order.
        # each tuple can/should be parallelised
        return result

    def add_step(self, step: 'PipelineStep'):
        if self.has_step(step.name):
            raise ValueError('conflict: pipeline definition already contained step {s}'.format(
                s=step.name
                )
            )
        self._steps_dict[step.name] = step

    def step(self, name):
        return self._steps_dict[name]

    def has_step(self, step_name):
        return step_name in self.step_names()

    def pr_repositories(self):
        # short-cut if we do not have trait 'pull-request'
        if not self.has_trait('pull-request'):
            return []

        pr_repo = self.pr_repository(self._main_repository_name)
        if pr_repo is None:
            return []
        return [pr_repo]

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

    def validate(self):
        for ps in self.steps():
            ps.validate()


class PipelineDefinition(object):
    def __init__(self):
        self._variants_dict = {}
        self._resource_registry = None

    def resource_registry(self):
        return self._resource_registry

    def variants(self):
        return self._variants_dict.values()

    def variant(self, name: str):
        return self._variants_dict[name]

