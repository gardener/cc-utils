<%def name="render_repositories(pipeline_definition, cfg_set)">
<%
from concourse.client.model import ResourceType
%>
<%namespace file="/resources/github.mako" import="*"/>
% for repo_cfg in pipeline_definition._resource_registry.resources(type_name=ResourceType.GIT.value):
${github_repo(
  repo_cfg=repo_cfg,
  cfg_set=cfg_set,
)}
% endfor
% for repo_cfg in pipeline_definition._resource_registry.resources(type_name=ResourceType.PULL_REQUEST.value):
<%
require_label = None
# if we have at least one pr-repo, there must be a pr-trait
for variant in pipeline_definition.variants():
  if variant.has_trait('pull-request'):
    require_label = variant.trait('pull-request').policies().require_label()
  concourse_cfg = cfg_set.concourse()
%>
${github_pr(
  repo_cfg=repo_cfg,
  cfg_set=cfg_set,
  require_label=require_label,
)}
% endfor
</%def>
