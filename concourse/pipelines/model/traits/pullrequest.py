from util import ensure_not_none

from concourse.pipelines.modelbase import Trait, TraitTransformer, ModelBase

class PullRequestPolicies(ModelBase):
    def require_label(self):
        return self.raw.get('require-label')


class PullRequestTrait(Trait):
    def repository_name(self):
        return self.raw.get('repo', 'source')

    def policies(self):
        policies_dict = self.raw.get('policies')
        if not policies_dict:
            policies_dict = {'require-label': 'ok-to-test'}

        return PullRequestPolicies(raw_dict=policies_dict)

    def transformer(self):
        return PullRequestTraitTransformer(trait=self, name=self.name)


class PullRequestTraitTransformer(TraitTransformer):
    def __init__(self, trait, *args, **kwargs):
        self.trait = trait
        super().__init__(*args, **kwargs)

    def process_pipeline_args(self, pipeline_args: 'PipelineArgs'):
        repo_name = self.trait.repository_name()

        # convert to PR
        pr_repo = pipeline_args.pr_repository(repo_name)
        pr_repo._trigger = True

        # patch-in the updated repository
        pipeline_args._repos_dict[repo_name] = pr_repo


