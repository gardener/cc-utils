# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

from util import ensure_not_none

from concourse.pipelines.modelbase import Trait, TraitTransformer, ModelBase, PipelineStep

class PullRequestPolicies(ModelBase):
    def require_label(self):
        return self.raw.get('require-label')


class PullRequestTrait(Trait):
    def repository_name(self):
        return self.raw.get('repo', 'source')

    def policies(self):
        policies_dict = self.raw.get('policies')
        if not policies_dict:
            policies_dict = {'require-label': 'reviewed/ok-to-test'}

        return PullRequestPolicies(raw_dict=policies_dict)

    def transformer(self):
        return PullRequestTraitTransformer(trait=self, name=self.name)


class PullRequestTraitTransformer(TraitTransformer):
    def __init__(self, trait, *args, **kwargs):
        self.trait = trait
        super().__init__(*args, **kwargs)

    def inject_steps(self):
        # declare no dependencies --> run asap, but do not block other steps
        rm_pr_label_step = PipelineStep(name='rm_pr_label', raw_dict={}, is_synthetic=True)
        yield rm_pr_label_step

    def process_pipeline_args(self, pipeline_args: 'PipelineArgs'):
        repo_name = self.trait.repository_name()

        # convert to PR
        pr_repo = pipeline_args.pr_repository(repo_name)
        pr_repo._trigger = True

        # patch-in the updated repository
        pipeline_args._repos_dict[repo_name] = pr_repo


