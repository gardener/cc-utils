import toposort

from concourse.pipelines.modelbase import (
    ModelBase,
    select_attr,
)
from concourse.pipelines.model.repositories import RepositoryConfig

class PipelineArgs(ModelBase):
    def __init__(self, raw_dict: dict, *args, **kwargs):
        self._main_repository_name = None
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
            name=name + '-pr',
            logical_name=name,
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

    def variant_names(self):
        return self._variants_dict.keys()

    def variants(self):
        return self._variants_dict.values()

    def variant(self, name):
        return self._variants_dict.get(name, None)

    def validate(self):
        for ps in self.steps():
            ps.validate()


class PipelineDefinition(object):
    _variants_dict = {}

    def variants(self):
        return self._variants_dict.values()

    def variant(self, name: str):
        return self._variants_dict[name]

    def git_resources(self):
        return self.git_resources_dict().values()

    def git_resources_dict(self):
        resources = {}
        for v in self.variants():
            for r in v.repositories():
                if r._is_pull_request:
                    continue
                resources[r.resource_name()] = r
        return resources

    def repositories(self):
        return self.git_resources()

    def repository(self, name):
        repos = filter(lambda r: r.logical_name() == name, self.repositories())
        return repos.__next__()

    def publish_repositories(self):
        repositories = {}
        for v in self.variants():
            for pub_repo in v.publish_repositories():
                repositories[pub_repo.resource_name()] = pub_repo
        return repositories.values()

    def pr_repositories(self):
        repositories = {}
        for v in self.variants():
            for pr_repo in v.pr_repositories():
                repositories[pr_repo.resource_name()] = pr_repo
        return repositories.values()

